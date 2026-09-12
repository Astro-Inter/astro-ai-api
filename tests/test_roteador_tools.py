from copy import deepcopy
from datetime import datetime

import pytest

from app.core import config
from app.modules.roteador import tools as router_tools
from app.modules.roteador.tools import enviar_mensagem


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.query = None
        self.parameters = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, query, parameters):
        self.query = query
        self.parameters = parameters

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows):
        self.db_cursor = FakeCursor(rows)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def cursor(self):
        return self.db_cursor


class FakeCollection:
    def __init__(self):
        self.documents = {}

    def insert_one(self, document):
        self.documents[document["_id"]] = deepcopy(document)

    def find_one(self, query):
        document = self.documents.get(query["_id"])
        if document is None:
            return None
        return document if all(document.get(key) == value for key, value in query.items()) else None


def tool_config(*, pending=None, explicit=False):
    return {"configurable": {
        "usuario_atual": {"uid": "firebase-sender", "role": "FUNCIONARIO"},
        "session_id": "session-1",
        "acao_pendente": pending,
        "confirmacao_explicita": explicit,
    }}


def configure_databases(monkeypatch, rows):
    connection = FakeConnection(rows)
    collection = FakeCollection()
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test:test@localhost/astro")
    monkeypatch.setattr(config, "MONGODB_URI", "mongodb://localhost:27017")
    monkeypatch.setattr(config, "MONGODB_DATABASE", "astro")
    monkeypatch.setattr(router_tools, "get_postgres_connection", lambda: connection)
    monkeypatch.setattr(router_tools, "get_messages_collection", lambda: collection)
    return connection, collection


def test_message_tool_hides_ids_and_authenticated_context():
    properties = enviar_mensagem.args_schema.model_json_schema()["properties"]
    assert set(properties) == {"destinatario", "mensagem", "confirmar_envio"}
    assert "uid" not in properties
    assert "id_envia" not in properties
    assert "id_recebe" not in properties


def test_message_preview_resolves_recipient_only_inside_sender_workspace(monkeypatch):
    connection, collection = configure_databases(
        monkeypatch, [(7, 21, "Lucas Souza", "lucas@empresa.com")],
    )

    result = enviar_mensagem.invoke(
        {"destinatario": "Lucas", "mensagem": "Olá!", "confirmar_envio": False},
        config=tool_config(),
    )

    assert result["status"] == "aguardando_confirmacao"
    assert result["rascunho"] == {
        "destinatario": {"nome": "Lucas Souza", "email": "lucas@empresa.com"},
        "mensagem": "Olá!",
    }
    assert result["acao_pendente"]["id_envia"] == 7
    assert result["acao_pendente"]["id_recebe"] == 21
    assert collection.documents == {}
    assert connection.db_cursor.parameters == ["firebase-sender", "%Lucas%"]
    assert "unidade_destinatario.workspace_id = remetente.workspace_id" in (
        connection.db_cursor.query
    )
    assert "destinatario.id_usuario <> remetente.id_usuario" in connection.db_cursor.query
    assert "destinatario.status = 'ATIVO'" in connection.db_cursor.query


def test_ambiguous_name_never_sends_and_requests_email(monkeypatch):
    _, collection = configure_databases(monkeypatch, [
        (7, 21, "Lucas Souza", "lucas.souza@empresa.com"),
        (7, 22, "Lucas Lima", "lucas.lima@empresa.com"),
    ])

    result = enviar_mensagem.invoke(
        {"destinatario": "Lucas", "mensagem": "Olá!"},
        config=tool_config(),
    )

    assert result["status"] == "ambiguo"
    assert [item["email"] for item in result["destinatarios"]] == [
        "lucas.souza@empresa.com", "lucas.lima@empresa.com",
    ]
    assert collection.documents == {}


def test_confirmation_requires_exact_pending_draft_and_explicit_user_message(monkeypatch):
    _, collection = configure_databases(
        monkeypatch, [(7, 21, "Lucas Souza", "lucas@empresa.com")],
    )
    preview = enviar_mensagem.invoke(
        {"destinatario": "lucas@empresa.com", "mensagem": "Olá!"},
        config=tool_config(),
    )

    result = enviar_mensagem.invoke(
        {
            "destinatario": "lucas@empresa.com",
            "mensagem": "Texto alterado sem nova prévia",
            "confirmar_envio": True,
        },
        config=tool_config(pending=preview["acao_pendente"], explicit=True),
    )

    assert result["status"] == "confirmacao_invalida"
    assert collection.documents == {}


def test_confirmed_message_uses_postgres_ids_and_is_written_once(monkeypatch):
    _, collection = configure_databases(
        monkeypatch, [(7, 21, "Lucas Souza", "lucas@empresa.com")],
    )
    preview = enviar_mensagem.invoke(
        {"destinatario": "lucas@empresa.com", "mensagem": "Olá! Tudo bem?"},
        config=tool_config(),
    )
    pending = preview["acao_pendente"]

    result = enviar_mensagem.invoke(
        {
            "destinatario": "lucas@empresa.com",
            "mensagem": "Olá! Tudo bem?",
            "confirmar_envio": True,
        },
        config=tool_config(pending=pending, explicit=True),
    )

    assert result["status"] == "ok"
    document = collection.documents[pending["id_mensagem"]]
    assert document["id_envia"] == 7
    assert document["id_recebe"] == 21
    assert document["mensagem"] == "Olá! Tudo bem?"
    assert isinstance(document["data"], datetime)
    assert document["data"].tzinfo is not None
    assert "firebase-sender" not in str(document)


def test_admin_cannot_use_workspace_message_tool(monkeypatch):
    monkeypatch.setattr(
        router_tools,
        "get_postgres_connection",
        lambda: pytest.fail("admin nao deve abrir conexao"),
    )
    result = enviar_mensagem.invoke(
        {"destinatario": "Lucas", "mensagem": "Olá!"},
        config={"configurable": {
            "usuario_atual": {"uid": "admin", "role": "ADMIN"},
            "session_id": "session-1",
        }},
    )
    assert result["status"] == "nao_aplicavel"
