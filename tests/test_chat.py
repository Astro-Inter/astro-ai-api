import asyncio
import json
from uuid import UUID, uuid4

import pytest
import httpx
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from langsmith import tracing_context

from app.api import auth
from app.core import config
from app.core.security import CurrentUser
from app.infrastructure.llm import models
from app.main import create_app
from app.modules.chat.errors import ChatError
from app.modules.chat.schemas import ChatRequest
from app.modules.chat.service import ChatService, recent_history
from app.modules.rh import tools as rh_tools
from app.modules.roteador import tools as router_tools
from app.modules.sst import tools as sst_tools
from memory_fakes import FakeAccessRoles, FakeFaqVectors, FakeSessions, FakeVectors


class FakeModel:
    def __init__(self, route="rh"):
        self.route = route
        self.calls = []
        self.replies = {}

    async def complete(self, agent, messages, *, json_mode=False):
        self.calls.append((agent, messages, json_mode))
        if agent in self.replies:
            reply = self.replies[agent]
            return reply.pop(0) if isinstance(reply, list) else reply
        if agent == "guardrail_entrada":
            return json.dumps({"decisao": "aprovar", "motivo": "legitimo", "mensagem": ""})
        if agent == "roteador":
            return "Olá! Como posso ajudar?" if self.route == "direta" else f"ROUTE={self.route}"
        if agent == "rh" and '"acao"' in messages[0].content:
            return json.dumps({
                "acao": "responder",
                "filtros": None,
                "resposta": {
                    "dominio": "rh", "intencao": "consultar", "status": "indisponivel",
                    "resposta": "A consulta está indisponível.",
                    "recomendacao": "Consulte a área responsável.",
                },
            })
        if agent == "sst" and '"acao"' in messages[0].content:
            return json.dumps({
                "acao": "responder",
                "filtros": None,
                "resposta": {
                    "dominio": "sst", "intencao": "orientar", "status": "concluido",
                    "resposta": "Orientação geral de segurança.",
                    "recomendacao": "Procure a equipe de SST.",
                },
            })
        if agent in {"rh", "sst", "agenda"}:
            return json.dumps({
                "dominio": agent, "intencao": "consultar", "status": "indisponivel",
                "resposta": "A consulta está indisponível.", "recomendacao": "Consulte a área responsável.",
            })
        if agent == "faq":
            return "O Astro centraliza orientações internas. Fonte: normas.pdf, página 1."
        if agent == "orquestrador":
            return "A consulta está indisponível. Consulte a área responsável."
        if agent == "juiz":
            return json.dumps({
                "status": "aprovado", "motivo": "Resposta sustentada pelos dados.",
                "problemas": [],
            })
        if agent == "guardrail_saida":
            data = json.loads(messages[-1].content.split("\n", 1)[1])
            return json.dumps({
                "status": "aprovado", "motivo": "Resposta compatível com os dados.",
                "resposta": data["resposta_candidata"],
            })
        raise AssertionError(f"Agente inesperado: {agent}")


@pytest.fixture(autouse=True)
def no_remote_tracing():
    # Nunca enviar conteúdo dos testes ao LangSmith, mesmo com .env habilitado.
    with tracing_context(enabled=False):
        yield


@pytest.fixture
def chat_client(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_DEV_LOGIN", False)
    application = create_app()
    model = FakeModel()
    application.state.chat_service = ChatService(
        model, repository=FakeSessions(), vectors=FakeVectors(), faq_vectors=FakeFaqVectors(),
    )
    application.state.access_roles = FakeAccessRoles()
    application.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(
        uid="user-a", role="FUNCIONARIO",
    )
    with TestClient(application) as client:
        yield client, model, application


@pytest.mark.parametrize("domain", ["rh", "sst", "agenda"])
def test_specialist_flow(chat_client, domain):
    client, model, application = chat_client
    model.route = domain
    response = client.post("/chat/messages", json={"message": "Preciso de uma orientação."})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["agentes_chamados"] == [
        "guardrail_entrada", "roteador", domain, "orquestrador", "juiz", "guardrail_saida",
    ]
    assert [call[0] for call in model.calls] == body["agentes_chamados"]
    history = application.state.chat_service.repository.docs[body["session_id"]]["mensagens"]
    assert history == [
        {"role": "human", "content": "Preciso de uma orientação."},
        {"role": "assistant", "content": body["resposta"]},
    ]
    system = model.calls[2][1][0].content
    assert '"uid": "user-a"' in system
    assert '"fuso": "America/Sao_Paulo"' in system
    assert (
        '"ferramentas_disponiveis": ["buscar_historico", "consultar_normas", '
        '"buscar_outros_usuarios", "buscar_meus_dados", "consultar_nrs", '
        '"consultar_nrs_obrigatorias", "consultar_situacao_nrs", '
        '"enviar_mensagem"]'
    ) in system
    if domain == "rh":
        assert "DECISÃO DE USO DA TOOL" in system
        assert "SAÍDA PARA O ORQUESTRADOR" not in system
    if domain == "sst":
        assert "DECISÃO DE USO DA TOOL" in system


def test_sst_agent_consults_multiple_nrs(chat_client, monkeypatch):
    client, model, _ = chat_client
    model.route = "sst"

    collection = type("Collection", (), {})()

    class Cursor:
        def sort(self, *_):
            return self
        def skip(self, _):
            return self
        def limit(self, _):
            return self
        def __iter__(self):
            return iter([
                {"_id": 1, "nome": "Disposições Gerais", "objetivo": "Objetivo 1"},
                {"_id": 6, "nome": "EPI", "objetivo": "Objetivo 6"},
            ])

    def find(query, projection):
        collection.query = query
        collection.projection = projection
        return Cursor()

    collection.find = find
    collection.count_documents = lambda _: 2
    monkeypatch.setattr(config, "MONGODB_URI", "mongodb://localhost:27017")
    monkeypatch.setattr(config, "MONGODB_DATABASE", "astro")
    monkeypatch.setattr(sst_tools, "get_collection", lambda: collection)
    model.replies["sst"] = json.dumps({
        "acao": "consultar_nrs",
        "filtros": {"numeros": [1, 6], "campos": ["objetivo"], "limite": 2},
        "resposta": None,
    })

    response = client.post(
        "/chat/messages", json={"message": "Quais os objetivos das NRs 1 e 6?"},
    )

    assert response.status_code == 200
    assert response.json()["agentes_chamados"] == [
        "guardrail_entrada", "roteador", "sst", "consultar_nrs",
        "juiz", "guardrail_saida",
    ]
    assert "NR-1" in response.json()["resposta"]
    assert "Objetivo 6" in response.json()["resposta"]
    assert "MongoDB `nrs`" in response.json()["resposta"]
    assert collection.query == {"_id": {"$in": [1, 6]}}
    assert [call[0] for call in model.calls] == [
        "guardrail_entrada", "roteador", "juiz",
    ]
    judge_call = next(call for call in model.calls if call[0] == "juiz")
    review = json.loads(judge_call[1][-1].content.split("\n", 1)[1])
    evidence = review["resultado"]["evidencia_tool"]["resultado"]
    assert evidence["numeros"] == [1, 6]
    assert "nrs" not in evidence
    assert "Objetivo 1" not in json.dumps(evidence)


def test_sst_agent_lists_all_current_nrs_with_compact_payload(chat_client, monkeypatch):
    client, model, _ = chat_client
    model.route = "sst"
    documents = [{
        "_id": number,
        "nome": f"Norma {number}",
        "revogada": False,
        "ultima_atualizacao": "01/01/2026",
        "descricao": "conteudo muito extenso " * 500,
    } for number in range(1, 36)]

    class Cursor:
        def __init__(self):
            self.offset = 0
            self.size = 50
        def sort(self, *_):
            return self
        def skip(self, value):
            self.offset = value
            return self
        def limit(self, value):
            self.size = value
            return self
        def __iter__(self):
            return iter(documents[self.offset:self.offset + self.size])

    class Collection:
        def count_documents(self, query):
            self.query = query
            return len(documents)
        def find(self, query, projection):
            self.query = query
            self.projection = projection
            return Cursor()

    collection = Collection()
    monkeypatch.setattr(config, "MONGODB_URI", "mongodb://localhost:27017")
    monkeypatch.setattr(config, "MONGODB_DATABASE", "astro")
    monkeypatch.setattr(sst_tools, "get_collection", lambda: collection)
    model.replies["sst"] = json.dumps({
        "acao": "consultar_nrs",
        "filtros": {
            "modo": "listar", "revogada": False, "pagina": 1, "limite": 50,
        },
        "resposta": None,
    })

    response = client.post("/chat/messages", json={
        "message": "Quais são todas as NRs hoje em dia que ainda estão em vigência?",
    })

    assert response.status_code == 200
    body = response.json()
    assert "Encontrei 35 NR(s). Página 1 de 1" in body["resposta"]
    assert "NR-35" in body["resposta"]
    assert "conteudo muito extenso" not in body["resposta"]
    assert collection.query == {"revogada": False}
    assert collection.projection == {
        "_id": 1, "nome": 1, "revogada": 1, "ultima_atualizacao": 1,
    }
    assert [call[0] for call in model.calls] == [
        "guardrail_entrada", "roteador", "juiz",
    ]
    judge_call = next(call for call in model.calls if call[0] == "juiz")
    judge_payload = judge_call[1][-1].content
    assert "conteudo muito extenso" not in judge_payload
    assert len(judge_payload) < 12000


def test_sst_agent_consults_mandatory_nrs_for_authenticated_user(chat_client, monkeypatch):
    client, model, _ = chat_client
    model.route = "sst"

    class Cursor:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute(self, query, parameters):
            self.query = query
            self.parameters = parameters
        def fetchall(self):
            return [
                ("Lucas", "Eletricista", "Matriz", 10, "Eletricidade", 24),
                ("Lucas", "Eletricista", "Matriz", 18, "Construção", 12),
            ]

    class Connection:
        def __init__(self):
            self.db_cursor = Cursor()
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def cursor(self):
            return self.db_cursor

    connection = Connection()
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test:test@localhost/astro")
    monkeypatch.setattr(sst_tools, "get_postgres_connection", lambda: connection)

    response = client.post("/chat/messages", json={
        "message": "Quais NRs são obrigatórias para minha função?",
    })

    assert response.status_code == 200
    body = response.json()
    assert body["agentes_chamados"] == [
        "guardrail_entrada", "roteador", "sst", "consultar_nrs_obrigatorias",
        "juiz", "guardrail_saida",
    ]
    assert "Para o cargo Eletricista" in body["resposta"]
    assert "NR-10" in body["resposta"] and "NR-18" in body["resposta"]
    assert "Fonte:" not in body["resposta"]
    assert "PostgreSQL" not in body["resposta"]
    assert [call[0] for call in model.calls] == [
        "guardrail_entrada", "roteador", "juiz",
    ]
    assert connection.db_cursor.parameters == ["user-a"]


def test_sst_agent_consults_nr_status_for_authenticated_user(chat_client, monkeypatch):
    from datetime import date

    client, model, _ = chat_client
    model.route = "sst"

    class Cursor:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute(self, query, parameters):
            self.query = query
            self.parameters = parameters
        def fetchall(self):
            return [(
                "Lucas", "Eletricista", "Matriz", 10, "Eletricidade",
                date(2027, 9, 12), None, None, "VIGENTE", "NENHUMA",
                date(2026, 9, 12),
            ), (
                "Lucas", "Eletricista", "Matriz", 18, "Construção",
                date(2026, 8, 1), None, None, "RENOVACAO_NECESSARIA", "RENOVAR",
                date(2026, 9, 12),
            )]

    class Connection:
        def __init__(self):
            self.db_cursor = Cursor()
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def cursor(self):
            return self.db_cursor

    connection = Connection()
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test:test@localhost/astro")
    monkeypatch.setattr(sst_tools, "get_postgres_connection", lambda: connection)

    response = client.post("/chat/messages", json={
        "message": "Quais das minhas NRs estão vigentes e quais preciso renovar?",
    })

    assert response.status_code == 200
    body = response.json()
    assert body["agentes_chamados"] == [
        "guardrail_entrada", "roteador", "sst", "consultar_situacao_nrs",
        "juiz", "guardrail_saida",
    ]
    assert "NR-10" in body["resposta"] and "situação: vigente" in body["resposta"]
    assert "NR-18" in body["resposta"] and "ação: renovar" in body["resposta"]
    assert "Fonte:" not in body["resposta"]
    assert "PostgreSQL" not in body["resposta"]
    assert [call[0] for call in model.calls] == [
        "guardrail_entrada", "roteador", "juiz",
    ]
    assert connection.db_cursor.parameters == ["user-a"]


def test_router_previews_and_sends_message_only_after_confirmation(chat_client, monkeypatch):
    client, model, application = chat_client

    class Collection:
        def __init__(self):
            self.documents = {}
        def insert_one(self, document):
            self.documents[document["_id"]] = document
        def find_one(self, query):
            return self.documents.get(query["_id"])

    collection = Collection()
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test:test@localhost/astro")
    monkeypatch.setattr(config, "MONGODB_URI", "mongodb://localhost:27017")
    monkeypatch.setattr(config, "MONGODB_DATABASE", "astro")
    monkeypatch.setattr(
        router_tools,
        "_resolver_destinatarios",
        lambda uid, recipient: [(7, 21, "Lucas Souza", "lucas@empresa.com")],
    )
    monkeypatch.setattr(router_tools, "get_messages_collection", lambda: collection)
    model.replies["roteador"] = (
        'MESSAGE={"destinatario":"lucas@empresa.com",'
        '"mensagem":"Olá! Podemos conversar amanhã?","confirmar_envio":true}'
    )

    preview = client.post("/chat/messages", json={
        "message": "Melhore e mande oi, podemos conversar amanhã para lucas@empresa.com",
    })

    assert preview.status_code == 200
    preview_body = preview.json()
    assert "Prévia para Lucas Souza" in preview_body["resposta"]
    assert "Olá! Podemos conversar amanhã?" in preview_body["resposta"]
    assert "Confirma o envio?" in preview_body["resposta"]
    assert collection.documents == {}
    session = application.state.chat_service.repository.docs[preview_body["session_id"]]
    pending = session["acao_pendente"]
    assert pending["id_envia"] == 7 and pending["id_recebe"] == 21
    assert preview_body["agentes_chamados"] == [
        "guardrail_entrada", "roteador", "enviar_mensagem", "juiz", "guardrail_saida",
    ]

    model.calls.clear()
    sent = client.post("/chat/messages", json={
        "message": "Sim, pode enviar.",
        "session_id": preview_body["session_id"],
    })

    assert sent.status_code == 200
    assert sent.json()["resposta"] == "Mensagem enviada para Lucas Souza (lucas@empresa.com)."
    assert sent.json()["agentes_chamados"] == [
        "guardrail_entrada", "roteador", "enviar_mensagem", "juiz", "guardrail_saida",
    ]
    assert [call[0] for call in model.calls] == ["guardrail_entrada", "juiz"]
    document = collection.documents[pending["id_mensagem"]]
    assert document["id_envia"] == 7 and document["id_recebe"] == 21
    assert document["mensagem"] == "Olá! Podemos conversar amanhã?"
    assert session["acao_pendente"] is None


@pytest.mark.parametrize("confirmation", [
    "Sim", "É isso mesmo que eu quero enviar", "Pode mandar", "Confirmo o envio",
])
def test_simple_message_previews_immediately_and_accepts_natural_confirmation(
    chat_client, monkeypatch, confirmation,
):
    client, model, application = chat_client

    class Collection:
        def __init__(self):
            self.documents = {}

        def insert_one(self, document):
            self.documents[document["_id"]] = document

    collection = Collection()
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test:test@localhost/astro")
    monkeypatch.setattr(config, "MONGODB_URI", "mongodb://localhost:27017")
    monkeypatch.setattr(config, "MONGODB_DATABASE", "astro")
    monkeypatch.setattr(
        router_tools, "_resolver_destinatarios",
        lambda uid, recipient: [(7, 21, "Rosa Maduda", "rosa@empresa.com")]
        if recipient == "Rosa Maduda" or recipient == "rosa@empresa.com" else [],
    )
    monkeypatch.setattr(router_tools, "get_messages_collection", lambda: collection)
    model.replies["roteador"] = "ROUTE=rh"  # O pedido evidente dispensa a classificação do LLM.

    preview = client.post("/chat/messages", json={
        "message": "Mande um oi para a Rosa Maduda, por favor",
    })

    assert preview.status_code == 200
    assert "Prévia para Rosa Maduda (rosa@empresa.com):\n\nOi\n\nConfirma" in (
        preview.json()["resposta"]
    )
    assert collection.documents == {}
    assert [call[0] for call in model.calls] == ["guardrail_entrada", "juiz"]

    session_id = preview.json()["session_id"]
    pending = application.state.chat_service.repository.docs[session_id]["acao_pendente"]
    sent = client.post("/chat/messages", json={
        "message": confirmation, "session_id": session_id,
    })

    assert sent.status_code == 200
    assert sent.json()["resposta"] == "Mensagem enviada para Rosa Maduda (rosa@empresa.com)."
    assert collection.documents[pending["id_mensagem"]]["mensagem"] == "Oi"
    assert application.state.chat_service.repository.docs[session_id]["acao_pendente"] is None


def test_simple_message_does_not_infer_send_from_negation_or_edit_request():
    from app.modules.chat.graph import _pedido_simples_de_mensagem

    assert _pedido_simples_de_mensagem("Não mande um oi para a Rosa Maduda") is None
    assert _pedido_simples_de_mensagem("Não quero mandar um oi para Rosa Maduda") is None
    assert _pedido_simples_de_mensagem("Como mandar um oi para Rosa Maduda?") is None
    assert _pedido_simples_de_mensagem("Melhore e mande 'oi' para Rosa Maduda") is None
    assert _pedido_simples_de_mensagem("Mande um oi para meu amigo") is None
    assert _pedido_simples_de_mensagem("Mande um oi para a Rosa Maduda").mensagem == "Oi"
    assert _pedido_simples_de_mensagem("Manda um oi pra Rosa Maduda").destinatario \
        == "Rosa Maduda"
    assert _pedido_simples_de_mensagem("Envie a mensagem 'Oi Duda' para Rosa Maduda") \
        .destinatario == "Rosa Maduda"


def test_rh_agent_uses_user_tool_and_receives_its_result(chat_client, monkeypatch):
    client, model, application = chat_client
    application.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(
        uid="user-a", role="GESTOR",
    )

    class Cursor:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute(self, query, parameters):
            self.query, self.parameters = query, parameters
        def fetchall(self):
            return [(
                "Ana", "ana@example.com", "FUNCIONARIO", "Analista",
                "Matriz", "HIBRIDO", "ATIVO",
            )]

    class Connection:
        def __init__(self):
            self.db_cursor = Cursor()
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def cursor(self):
            return self.db_cursor

    connection = Connection()
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test:test@localhost/astro")
    monkeypatch.setattr(rh_tools, "get_conn", lambda: connection)
    model.replies["rh"] = json.dumps({
        "acao": "buscar_outros_usuarios",
        "filtros": {"status": ["ATIVO"]},
        "resposta": None,
    })

    response = client.post("/chat/messages", json={"message": "Quais funcionários estão ativos?"})

    assert response.status_code == 200
    assert response.json()["agentes_chamados"] == [
        "guardrail_entrada", "roteador", "rh", "buscar_outros_usuarios",
        "juiz", "guardrail_saida",
    ]
    assert [call[0] for call in model.calls] == [
        "guardrail_entrada", "roteador", "rh", "juiz",
    ]
    assert connection.db_cursor.parameters == ["user-a", ["GESTOR", "FUNCIONARIO"], "user-a", ["ATIVO"], 20]
    assert [call[0] for call in model.calls].count("rh") == 1
    judge_call = next(call for call in model.calls if call[0] == "juiz")
    tool_result = json.loads(judge_call[1][-1].content.split("\n", 1)[1])
    assert tool_result["resultado"]["evidencia_tool"]["resultado"]["usuarios"][0][
        "email"
    ] == "ana@example.com"
    evidence = json.loads(judge_call[1][-1].content.split("\n", 1)[1])["resultado"]
    assert evidence["evidencia_tool"]["nome"] == "buscar_outros_usuarios"
    assert evidence["evidencia_tool"]["resultado"]["status"] == "ok"


def test_rh_agent_uses_current_user_tool(chat_client, monkeypatch):
    client, model, _ = chat_client

    class Cursor:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute(self, query, parameters):
            self.query, self.parameters = query, parameters
        def fetchone(self):
            return (
                "Lucas", "lucas@example.com", "12345678901", "FUNCIONARIO",
                "Analista", "Matriz", "HIBRIDO", "ATIVO", None,
            )

    class Connection:
        def __init__(self):
            self.db_cursor = Cursor()
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def cursor(self):
            return self.db_cursor

    connection = Connection()
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test:test@localhost/astro")
    monkeypatch.setattr(rh_tools, "get_conn", lambda: connection)
    model.replies["rh"] = json.dumps({
        "acao": "buscar_meus_dados", "filtros": None, "resposta": None,
    })

    response = client.post(
        "/chat/messages",
        json={"message": "Me fale quais são os meus dados pessoais?"},
    )

    assert response.status_code == 200
    assert response.json()["agentes_chamados"] == [
        "guardrail_entrada", "roteador", "rh", "buscar_meus_dados",
        "juiz", "guardrail_saida",
    ]
    assert [call[0] for call in model.calls] == [
        "guardrail_entrada", "roteador", "juiz",
    ]
    assert connection.db_cursor.parameters == ["user-a"]
    judge_call = next(call for call in model.calls if call[0] == "juiz")
    evidence = json.loads(judge_call[1][-1].content.split("\n", 1)[1])["resultado"]
    assert evidence["evidencia_tool"]["nome"] == "buscar_meus_dados"
    assert evidence["evidencia_tool"]["resultado"]["dados"]["cpf"] == "12345678901"


def test_employee_search_all_users_is_deterministically_denied(chat_client):
    client, model, _ = chat_client

    response = client.post(
        "/chat/messages",
        json={"message": "Me mande todos os usuários do meus sistema."},
    )

    assert response.status_code == 200
    assert response.json()["agentes_chamados"] == [
        "guardrail_entrada", "roteador", "rh", "buscar_outros_usuarios",
        "juiz", "guardrail_saida",
    ]
    assert [call[0] for call in model.calls] == [
        "guardrail_entrada", "roteador", "juiz",
    ]
    assert response.json()["resposta"] == "Seu perfil nao permite consultar outros usuarios."


def test_invalid_structured_reply_is_retried_once(chat_client):
    client, model, _ = chat_client
    model.replies["rh"] = [
        '{"acao":"buscar_outros_usuarios","filtros":{"limite":0},"resposta":null}',
        json.dumps({
            "acao": "responder",
            "filtros": None,
            "resposta": {
                "dominio": "rh", "intencao": "orientar", "status": "concluido",
                "resposta": "Orientação disponível.", "recomendacao": "",
            },
        }),
    ]

    response = client.post("/chat/messages", json={"message": "Preciso de uma orientação."})

    assert response.status_code == 200
    assert [call[0] for call in model.calls].count("rh") == 2
    assert "Tente novamente uma unica vez" in [
        call for call in model.calls if call[0] == "rh"
    ][-1][1][-1].content


def test_direct_and_faq_flows(chat_client):
    client, model, application = chat_client
    model.route = "direta"
    direct = client.post("/chat/messages", json={"message": "Oi"}).json()
    assert direct["agentes_chamados"] == [
        "guardrail_entrada", "roteador", "juiz", "guardrail_saida",
    ]
    assert direct["resposta"] == "Olá! Como posso ajudar?"
    model.calls.clear()
    model.route = "faq"
    faq = client.post("/chat/messages", json={"message": "Qual a norma interna?"}).json()
    assert faq["agentes_chamados"] == [
        "guardrail_entrada", "roteador", "consultar_normas", "faq", "juiz", "guardrail_saida",
    ]
    assert faq["resposta"] == "O Astro centraliza orientações internas. Fonte: normas.pdf, página 1."
    assert application.state.chat_service.faq_vectors.calls == ["Qual a norma interna?"]
    assert [call[0] for call in model.calls] == [
        "guardrail_entrada", "roteador", "faq", "juiz", "guardrail_saida",
    ]
    faq_call = next(call for call in model.calls if call[0] == "faq")
    retrieved = json.loads(faq_call[1][-1].content.split("\n", 1)[1])
    assert retrieved["resultado"]["trechos"][0]["fonte"] == "normas.pdf"
    judge_call = next(call for call in model.calls if call[0] == "juiz")
    judged = json.loads(judge_call[1][-1].content.split("\n", 1)[1])
    assert judged["resultado"]["trechos"][0]["fonte"] == "normas.pdf"
    assert judged["resposta_candidata"] == faq["resposta"]
    session = application.state.chat_service.repository.docs[faq["session_id"]]
    assert session["mensagens"][-1]["content"] == faq["resposta"]


def test_faq_without_relevant_chunks_does_not_call_model(chat_client):
    client, model, application = chat_client
    model.route = "faq"
    application.state.chat_service.faq_vectors.results = []
    response = client.post("/chat/messages", json={"message": "Pergunta ausente"})
    assert response.status_code == 200
    assert response.json()["resposta"] == (
        "Não encontrei essa informação nas normas disponibilizadas ao Astro."
    )
    assert [call[0] for call in model.calls] == [
        "guardrail_entrada", "roteador", "juiz", "guardrail_saida",
    ]


@pytest.mark.parametrize("decision", ["bloquear", "esclarecer"])
def test_input_guard_stops_graph(chat_client, decision):
    client, model, application = chat_client
    model.replies["guardrail_entrada"] = json.dumps({
        "decisao": decision, "motivo": "contexto_insuficiente", "mensagem": "Explique seu pedido.",
    })
    response = client.post("/chat/messages", json={"message": "Pedido"})
    assert response.status_code == 200
    assert response.json()["agentes_chamados"] == ["guardrail_entrada"]
    assert response.json()["resposta"] == "Explique seu pedido."
    session = application.state.chat_service.repository.docs[response.json()["session_id"]]
    assert bool(session["mensagens"]) == (decision == "esclarecer")


@pytest.mark.parametrize("status", ["corrigido", "bloqueado"])
def test_output_guard_replaces_candidate(chat_client, status):
    client, model, application = chat_client
    model.replies["juiz"] = json.dumps({
        "status": "revisar", "motivo": "Resposta precisa de revisão.",
        "problemas": ["Resposta não confirmada."],
    })
    model.replies["guardrail_saida"] = json.dumps({
        "status": status, "motivo": "Falta evidência.", "resposta": "Resposta revisada.",
    })
    response = client.post("/chat/messages", json={"message": "Pedido"})
    assert response.json()["resposta"] == "Resposta revisada."
    session = application.state.chat_service.repository.docs[response.json()["session_id"]]
    assert bool(session["mensagens"]) == (status == "corrigido")


@pytest.mark.parametrize("agent,reply", [
    ("guardrail_entrada", "não é JSON"),
    ("guardrail_entrada", '{"decisao":"aprovar","motivo":"legitimo","mensagem":"Erro"}'),
    ("roteador", "ROUTE=desconhecido"),
    ("roteador", "Vou encaminhar: ROUTE=rh"),
    ("rh", '{"dominio":"rh"}'),
    ("rh", json.dumps({"dominio": "sst", "intencao": "orientar", "status": "concluido",
                       "resposta": "Texto", "recomendacao": ""})),
    ("rh", json.dumps({"dominio": "rh", "intencao": "atualizar", "status": "concluido",
                       "resposta": "Alteração feita", "recomendacao": ""})),
    ("orquestrador", "   "),
    ("juiz", "não é JSON"),
    ("juiz", json.dumps({"status": "aprovado", "motivo": "ok", "problemas": ["erro"]})),
    ("guardrail_saida", json.dumps({"status": "aprovado", "motivo": "ok", "resposta": "Alterada"})),
    ("guardrail_saida", "```json\n{}\n```"),
])
def test_invalid_agent_reply_fails_closed(chat_client, agent, reply):
    client, model, application = chat_client
    model.replies[agent] = reply
    if agent == "guardrail_saida":
        model.replies["juiz"] = json.dumps({
            "status": "revisar", "motivo": "Resposta precisa de revisão.",
            "problemas": ["Resposta não confirmada."],
        })
    response = client.post("/chat/messages", json={"message": "Pedido"})
    assert response.status_code == 502
    assert response.json() == {"detail": "A IA retornou uma resposta invalida. Tente novamente."}
    assert all(not doc["mensagens"] and "lock_token" not in doc
               for doc in application.state.chat_service.repository.docs.values())
    assert application.state.chat_service.active_requests == 0


def test_session_history_and_ownership(chat_client):
    client, model, application = chat_client
    first = client.post("/chat/messages", json={"message": "Primeira mensagem"}).json()
    model.calls.clear()
    second = client.post("/chat/messages", json={
        "message": "E agora?", "session_id": first["session_id"],
    })
    assert second.status_code == 200
    assert second.json()["session_id"] == first["session_id"]
    messages = next(call for call in model.calls if call[0] == "roteador")[1]
    assert [message.content for message in messages[1:]] == [
        "Primeira mensagem", first["resposta"], "E agora?",
    ]
    assert '"ultima_rota": "rh"' in messages[0].content
    assert '"fuso": "America/Sao_Paulo"' in messages[0].content
    application.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(
        uid="user-b", role="FUNCIONARIO",
    )
    model.calls.clear()
    forbidden = client.post("/chat/messages", json={"message": "Oi", "session_id": first["session_id"]})
    unknown = client.post("/chat/messages", json={"message": "Oi", "session_id": str(uuid4())})
    assert forbidden.status_code == 404
    assert unknown.status_code == 200
    model.calls.clear()
    new = client.post("/chat/messages", json={"message": "Nova conversa"})
    assert new.status_code == 200
    assert len(model.calls[0][1]) == 2


def test_each_turn_resets_intermediate_results(chat_client):
    client, model, application = chat_client
    first = client.post("/chat/messages", json={"message": "RH"}).json()
    model.route = "direta"
    model.calls.clear()
    second = client.post("/chat/messages", json={"message": "Oi", "session_id": first["session_id"]})
    assert second.json()["agentes_chamados"] == [
        "guardrail_entrada", "roteador", "juiz", "guardrail_saida",
    ]
    review = json.loads(model.calls[-1][1][-1].content.split("\n", 1)[1])
    assert review["resultado"] == {}
    session = application.state.chat_service.repository.docs[first["session_id"]]
    assert len(session["mensagens"]) == 4


def test_judge_flags_response_and_guardrail_must_change_it(chat_client):
    client, model, application = chat_client
    model.route = "direta"
    model.replies["juiz"] = json.dumps({
        "status": "revisar", "motivo": "Afirmação sem evidência.",
        "problemas": ["A candidata afirma uma operação não confirmada."],
    })
    model.replies["guardrail_saida"] = json.dumps({
        "status": "corrigido", "motivo": "Afirmação não confirmada removida.",
        "resposta": "Não consigo confirmar que essa operação foi realizada.",
    })
    response = client.post("/chat/messages", json={"message": "A operação terminou?"})
    assert response.status_code == 200
    assert response.json()["resposta"] == "Não consigo confirmar que essa operação foi realizada."
    judge_data = json.loads(model.calls[-2][1][-1].content.split("\n", 1)[1])
    assert judge_data["resposta_candidata"] == "Olá! Como posso ajudar?"
    guard_data = json.loads(model.calls[-1][1][-1].content.split("\n", 1)[1])
    assert guard_data["avaliacao_juiz"]["status"] == "revisar"
    assert application.state.chat_service.repository.docs[response.json()["session_id"]]["mensagens"]


@pytest.mark.parametrize("guard_status", ["aprovado", "corrigido"])
def test_guardrail_cannot_ignore_negative_judge(chat_client, guard_status):
    client, model, application = chat_client
    model.route = "direta"
    model.replies["juiz"] = json.dumps({
        "status": "rejeitado", "motivo": "Sem base.",
        "problemas": ["Resposta sem evidência."],
    })
    model.replies["guardrail_saida"] = json.dumps({
        "status": guard_status, "motivo": "Ignorando avaliação.",
        "resposta": "Olá! Como posso ajudar?",
    })
    response = client.post("/chat/messages", json={"message": "Pedido"})
    assert response.status_code == 502
    assert all(not doc["mensagens"] for doc in application.state.chat_service.repository.docs.values())


@pytest.mark.parametrize("body", [
    {}, {"message": ""}, {"message": "  "}, {"message": "x" * 4001},
    {"message": "Oi", "uid": "outro"}, {"message": "Oi", "workspace_id": "outro"},
    {"message": "Oi", "historico": []}, {"message": "Oi", "session_id": "invalid"},
    {"message": "Oi", "timezone": "UTC"},
])
def test_request_validation(chat_client, body):
    client, model, _ = chat_client
    assert client.post("/chat/messages", json=body).status_code == 422
    assert not model.calls


def test_chat_requires_verified_firebase_token(chat_client, monkeypatch):
    client, model, application = chat_client
    application.dependency_overrides.clear()
    assert client.post("/chat/messages", json={"message": "Oi"}).status_code == 401
    assert client.post("/chat/messages", json={"message": "Oi"}, headers={
        "X-Dev-Auth-Token": "fake-test-password",
    }).status_code == 401
    def invalid_token(token):
        raise auth.auth.InvalidIdTokenError("Token invalido")
    monkeypatch.setattr(auth, "verify_firebase_id_token", invalid_token)
    assert client.post("/chat/messages", json={"message": "Oi"}, headers={
        "Authorization": "Bearer invalid-token",
    }).status_code == 401
    assert not model.calls
    monkeypatch.setattr(auth, "verify_firebase_id_token", lambda token: {"uid": "firebase-user"})
    assert client.post("/chat/messages", json={"message": "Oi"}, headers={
        "Authorization": "Bearer fake-valid-token",
    }).status_code == 200
    assert '"uid": "firebase-user"' in model.calls[0][1][0].content
    assert '"role": "FUNCIONARIO"' in model.calls[0][1][0].content
    assert application.state.access_roles.calls == ["firebase-user"]
    assert "fake-valid-token" not in str(model.calls)


def test_persistent_history_with_bounded_model_context():
    async def scenario():
        repository = FakeSessions()
        service = ChatService(FakeModel("direta"), repository=repository, vectors=FakeVectors())
        user = CurrentUser(uid="user-a", role="FUNCIONARIO")
        first = await service.chat(ChatRequest(message="Olá"), user)
        for _ in range(12):
            await service.chat(ChatRequest(message="x" * 4000, session_id=first.session_id), user)
        messages = repository.docs[str(first.session_id)]["mensagens"]
        assert len(messages) == 26
        history = recent_history(messages)
        assert len(history) <= 20 and len(history) % 2 == 0
        assert sum(len(message["content"]) for message in history) <= 24000
        other_model = FakeModel("direta")
        restarted = ChatService(other_model, repository=repository, vectors=FakeVectors())
        await restarted.chat(ChatRequest(message="Continuação", session_id=first.session_id), user)
        router_messages = next(call for call in other_model.calls if call[0] == "roteador")[1]
        assert len(router_messages) > 2
        assert len(repository.docs) == 1
    asyncio.run(scenario())


def test_concurrency_and_timeout_release_session():
    async def scenario():
        model = FakeModel("direta")
        service = ChatService(model, repository=FakeSessions(), vectors=FakeVectors())
        user = CurrentUser(uid="user-a", role="FUNCIONARIO")
        first = await service.chat(ChatRequest(message="Oi"), user)
        entered, release = asyncio.Event(), asyncio.Event()
        original = model.complete
        async def waiting(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)
        model.complete = waiting
        request = ChatRequest(message="Nova", session_id=first.session_id)
        pending = asyncio.create_task(service.chat(request, user))
        await entered.wait()
        with pytest.raises(ChatError) as error:
            await service.chat(request, user)
        assert error.value.status_code == 409
        release.set()
        await pending
        assert service.active_requests == 0
        assert not ("lock_token" in service.repository.docs[str(first.session_id)])
        release.clear()
        service.request_timeout = 0.01
        history = list(service.repository.docs[str(first.session_id)]["mensagens"])
        with pytest.raises(ChatError) as error:
            await service.chat(request, user)
        assert error.value.status_code == 504
        assert service.repository.docs[str(first.session_id)]["mensagens"] == history
        assert not ("lock_token" in service.repository.docs[str(first.session_id)])
        with pytest.raises(ChatError):
            await service.chat(ChatRequest(message="Nova sessão"), user)
        assert len(service.repository.docs) == 2
        assert all("lock_token" not in doc for doc in service.repository.docs.values())
        assert service.active_requests == 0
    asyncio.run(scenario())


def test_model_error_hides_provider_details(monkeypatch):
    class BrokenProvider:
        def bind(self, **kwargs):
            return self
        async def ainvoke(self, *args, **kwargs):
            raise RuntimeError("internal-url private-key provider-error")
    monkeypatch.setattr(models, "get_model", lambda specialist: BrokenProvider())
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "")
    with pytest.raises(ChatError) as error:
        asyncio.run(models.LanguageModels().complete("rh", [], json_mode=True))
    assert error.value.status_code == 503
    assert "private-key" not in str(error.value)


def test_specialist_falls_back_from_mistral_to_groq(monkeypatch):
    calls = []

    class BrokenMistral:
        def bind(self, **kwargs):
            return self
        async def ainvoke(self, *args, **kwargs):
            calls.append("mistral")
            raise RuntimeError("429 rate limit")

    class WorkingGroq:
        def bind(self, **kwargs):
            calls.append(("groq_bind", kwargs))
            return self
        async def ainvoke(self, *args, **kwargs):
            calls.append("groq")
            return AIMessage(content='{"status":"ok"}')

    monkeypatch.setattr(config, "MISTRAL_API_KEY", "fake-mistral")
    monkeypatch.setattr(config, "GROQ_API_KEY", "fake-groq")
    monkeypatch.setattr(models, "get_model", lambda specialist: BrokenMistral())
    monkeypatch.setattr(models, "ChatGroq", lambda **kwargs: WorkingGroq())

    result = asyncio.run(models.LanguageModels().complete("rh", [], json_mode=True))

    assert result == '{"status":"ok"}'
    assert calls == [
        "mistral",
        ("groq_bind", {"response_format": {"type": "json_object"}}),
        "groq",
    ]


def test_groq_400_in_json_mode_retries_with_local_validation(monkeypatch):
    calls = []

    class JsonModeFailure(Exception):
        status_code = 400

    class BoundGroq:
        async def ainvoke(self, *args, **kwargs):
            calls.append("json_mode")
            raise JsonModeFailure()

    class Groq:
        def bind(self, **kwargs):
            calls.append(("bind", kwargs))
            return BoundGroq()
        async def ainvoke(self, *args, **kwargs):
            calls.append("plain")
            return AIMessage(content='{"status":"aprovado"}')

    monkeypatch.setattr(config, "MISTRAL_API_KEY", "")
    monkeypatch.setattr(models, "get_model", lambda specialist: Groq())

    result = asyncio.run(models.LanguageModels().complete(
        "guardrail_saida", [], json_mode=True,
    ))

    assert result == '{"status":"aprovado"}'
    assert calls == [
        ("bind", {"response_format": {"type": "json_object"}}),
        "json_mode",
        "plain",
    ]


def test_missing_llm_configuration(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "")
    models.get_model.cache_clear()
    try:
        with pytest.raises(ChatError) as error:
            models.get_model(False)
        assert error.value.status_code == 503
    finally:
        models.get_model.cache_clear()


@pytest.mark.parametrize("agent,mistral_key,expected_model,host", [
    ("roteador", "fake-mistral", models.GROQ_FAST_MODEL, "api.groq.com"),
    ("juiz", "fake-mistral", models.GROQ_FAST_MODEL, "api.groq.com"),
    ("rh", "fake-mistral", models.MISTRAL_SPECIALIST_MODEL, "api.mistral.ai"),
    ("agenda", "", models.GROQ_FAST_MODEL, "api.groq.com"),
])
def test_provider_sdks_with_mock_http(monkeypatch, agent, mistral_key, expected_model, host):
    monkeypatch.setattr(config, "GROQ_API_KEY", "fake-groq")
    monkeypatch.setattr(config, "MISTRAL_API_KEY", mistral_key)
    for name in ("MISTRAL_BASE_URL", "GROQ_BASE_URL", "GROQ_API_BASE"):
        monkeypatch.delenv(name, raising=False)
    requests = []
    async def send(client, request, **kwargs):
        requests.append(request)
        body = json.loads(request.content)
        assert request.url.host == host
        assert body["model"] == expected_model
        assert body["response_format"] == {"type": "json_object"}
        assert body["messages"] == [{"role": "user", "content": "Responda JSON."}]
        assert body["max_tokens"] == 1600
        return httpx.Response(200, request=request, json={
            "id": "test-completion", "object": "chat.completion", "created": 1,
            "model": expected_model,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": '{"ok":true}',
            }}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        })
    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    models.get_model.cache_clear()
    try:
        response = asyncio.run(models.LanguageModels().complete(
            agent, [HumanMessage(content="Responda JSON.")], json_mode=True,
        ))
        assert response == '{"ok":true}'
        assert len(requests) == 1
    finally:
        models.get_model.cache_clear()
