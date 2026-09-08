import asyncio
import json
from uuid import UUID, uuid4

import pytest
import httpx
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage
from langsmith import tracing_context

from app.api import auth
from app.core import config
from app.core.security import CurrentUser
from app.infrastructure.llm import models
from app.main import create_app
from app.modules.chat.errors import ChatError
from app.modules.chat.schemas import ChatRequest
from app.modules.chat.service import ChatService, recent_history
from memory_fakes import FakeSessions, FakeVectors


class FakeModel:
    def __init__(self, route="rh"):
        self.route = route
        self.calls = []
        self.replies = {}

    async def complete(self, agent, messages, *, json_mode=False):
        self.calls.append((agent, messages, json_mode))
        if agent in self.replies:
            return self.replies[agent]
        if agent == "guardrail_entrada":
            return json.dumps({"decisao": "aprovar", "motivo": "legitimo", "mensagem": ""})
        if agent == "roteador":
            return "Olá! Como posso ajudar?" if self.route == "direta" else f"ROUTE={self.route}"
        if agent in {"rh", "sst", "agenda"}:
            return json.dumps({
                "dominio": agent, "intencao": "consultar", "status": "indisponivel",
                "resposta": "A consulta está indisponível.", "recomendacao": "Consulte a área responsável.",
            })
        if agent == "orquestrador":
            return "A consulta está indisponível. Consulte a área responsável."
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
    application.state.chat_service = ChatService(model, repository=FakeSessions(), vectors=FakeVectors())
    application.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(uid="user-a")
    with TestClient(application) as client:
        yield client, model, application


@pytest.mark.parametrize("domain", ["rh", "sst", "agenda"])
def test_specialist_flow(chat_client, domain):
    client, model, application = chat_client
    model.route = domain
    response = client.post("/chat/messages", json={"message": "Consulte meus dados."})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["agentes_chamados"] == [
        "guardrail_entrada", "roteador", domain, "orquestrador", "guardrail_saida",
    ]
    assert [call[0] for call in model.calls] == body["agentes_chamados"]
    history = application.state.chat_service.repository.docs[body["session_id"]]["mensagens"]
    assert history == [
        {"role": "human", "content": "Consulte meus dados."},
        {"role": "assistant", "content": body["resposta"]},
    ]
    system = model.calls[2][1][0].content
    assert '"uid": "user-a"' in system
    assert '"fuso": "America/Sao_Paulo"' in system
    assert '"ferramentas_disponiveis": ["buscar_historico"]' in system


def test_direct_and_faq_flows(chat_client):
    client, model, _ = chat_client
    model.route = "direta"
    direct = client.post("/chat/messages", json={"message": "Oi"}).json()
    assert direct["agentes_chamados"] == ["guardrail_entrada", "roteador", "guardrail_saida"]
    assert direct["resposta"] == "Olá! Como posso ajudar?"
    model.calls.clear()
    model.route = "faq"
    faq = client.post("/chat/messages", json={"message": "Qual a norma interna?"}).json()
    assert faq["agentes_chamados"] == ["guardrail_entrada", "roteador", "faq"]
    assert "indisponível" in faq["resposta"]
    # Sem retriever, não gastar uma chamada nem inventar conteúdo de normas.
    assert [call[0] for call in model.calls] == ["guardrail_entrada", "roteador"]


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
    ("guardrail_saida", json.dumps({"status": "aprovado", "motivo": "ok", "resposta": "Alterada"})),
    ("guardrail_saida", "```json\n{}\n```"),
])
def test_invalid_agent_reply_fails_closed(chat_client, agent, reply):
    client, model, application = chat_client
    model.replies[agent] = reply
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
    messages = model.calls[0][1]
    assert [message.content for message in messages[1:]] == [
        "Primeira mensagem", first["resposta"], "E agora?",
    ]
    assert '"ultima_rota": "rh"' in messages[0].content
    assert '"fuso": "America/Sao_Paulo"' in messages[0].content
    application.dependency_overrides[auth.get_current_user] = lambda: CurrentUser(uid="user-b")
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
    assert second.json()["agentes_chamados"] == ["guardrail_entrada", "roteador", "guardrail_saida"]
    review = json.loads(model.calls[-1][1][-1].content.split("\n", 1)[1])
    assert review["resultado"] == {}
    session = application.state.chat_service.repository.docs[first["session_id"]]
    assert len(session["mensagens"]) == 4


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
    assert "fake-valid-token" not in str(model.calls)


def test_persistent_history_with_bounded_model_context():
    async def scenario():
        repository = FakeSessions()
        service = ChatService(FakeModel("direta"), repository=repository, vectors=FakeVectors())
        user = CurrentUser(uid="user-a")
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
        assert len(other_model.calls[0][1]) > 2
        assert len(repository.docs) == 1
    asyncio.run(scenario())


def test_concurrency_and_timeout_release_session():
    async def scenario():
        model = FakeModel("direta")
        service = ChatService(model, repository=FakeSessions(), vectors=FakeVectors())
        user = CurrentUser(uid="user-a")
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
    with pytest.raises(ChatError) as error:
        asyncio.run(models.LanguageModels().complete("rh", [], json_mode=True))
    assert error.value.status_code == 503
    assert "private-key" not in str(error.value)


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
    ("rh", "fake-mistral", models.MISTRAL_SPECIALIST_MODEL, "api.mistral.ai"),
    ("agenda", "", models.GROQ_SPECIALIST_MODEL, "api.groq.com"),
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
