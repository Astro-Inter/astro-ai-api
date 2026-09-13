from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.core import config
from app.modules.roteador import tools as router_tools
from app.modules.roteador.tools import (
    consultar_conversas, consultar_notificacoes, enviar_mensagem,
)


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

    def fetchone(self):
        return self.rows[0] if self.rows else None


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

    def _matching(self, query):
        return [
            document for document in self.documents.values()
            if any(all(document.get(key) == value for key, value in pair.items())
                   for pair in query["$or"])
        ]

    def count_documents(self, query):
        return len(self._matching(query))

    def find(self, query, projection):
        return FakeMessagesCursor(self._matching(query))


class FakeNotificationsCollection(FakeCollection):
    def _matching(self, query):
        return [
            document for document in self.documents.values()
            if document.get("id_usuario") == query["id_usuario"]
        ]


class FakeMessagesCursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, fields):
        for key, direction in reversed(fields):
            self.documents.sort(key=lambda document: document[key], reverse=direction == -1)
        return self

    def skip(self, count):
        self.documents = self.documents[count:]
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    def __iter__(self):
        return iter(self.documents)


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


def test_conversation_tool_exposes_only_name_and_pagination_args():
    properties = consultar_conversas.args_schema.model_json_schema()["properties"]
    assert set(properties) == {"pessoa", "pagina", "limite"}


def test_conversation_reads_both_directions_only_for_authenticated_pair(monkeypatch):
    connection, collection = configure_databases(
        monkeypatch, [(7, 21, "Rosa Maduda", "rosa@empresa.com")],
    )
    timestamp = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    for identifier, sender, recipient, body in [
        ("1", 7, 21, "Oi Rosa"),
        ("2", 21, 7, "Oi Lucas"),
        ("3", 99, 21, "Segredo de outra pessoa"),
        ("4", 7, 99, "Outro contato"),
    ]:
        collection.insert_one({
            "_id": identifier, "id_envia": sender, "id_recebe": recipient,
            "mensagem": body, "data": timestamp,
        })

    result = consultar_conversas.invoke(
        {"pessoa": "rosa@empresa.com"}, config=tool_config(),
    )

    assert result["status"] == "ok"
    assert result["total"] == 2
    assert [message["direcao"] for message in result["mensagens"]] == [
        "recebida", "enviada",
    ]
    assert [message["mensagem"] for message in result["mensagens"]] == [
        "Oi Lucas", "Oi Rosa",
    ]
    assert "Segredo" not in str(result)
    assert connection.db_cursor.parameters == ["firebase-sender", "rosa@empresa.com"]
    assert "unidade_destinatario.workspace_id = remetente.workspace_id" in (
        connection.db_cursor.query
    )


def test_conversation_is_paginated_and_marks_long_excerpts(monkeypatch):
    _, collection = configure_databases(
        monkeypatch, [(7, 21, "Rosa Maduda", "rosa@empresa.com")],
    )
    for index in range(4):
        collection.insert_one({
            "_id": str(index), "id_envia": 7, "id_recebe": 21,
            "mensagem": "x" * 600 if index == 1 else f"Mensagem {index}",
            "data": datetime(2026, 9, 12, index, tzinfo=timezone.utc),
        })

    result = consultar_conversas.invoke(
        {"pessoa": "Rosa", "pagina": 2, "limite": 2}, config=tool_config(),
    )

    assert result["total"] == 4
    assert result["total_paginas"] == 2
    assert [item["mensagem"] for item in result["mensagens"]] == [
        "x" * 500, "Mensagem 0",
    ]
    assert result["mensagens"][0]["trecho"] is True


def test_conversation_requires_unique_person_and_does_not_read_mongo(monkeypatch):
    _, collection = configure_databases(monkeypatch, [
        (7, 21, "Rosa Maduda", "rosa1@empresa.com"),
        (7, 22, "Rosa Maria", "rosa2@empresa.com"),
    ])
    monkeypatch.setattr(
        collection, "count_documents", lambda _: pytest.fail("nao deve ler Mongo"),
    )

    result = consultar_conversas.invoke({"pessoa": "Rosa"}, config=tool_config())

    assert result["status"] == "ambiguo"
    assert len(result["pessoas"]) == 2


def test_conversation_reports_empty_history_without_inventing_messages(monkeypatch):
    configure_databases(
        monkeypatch, [(7, 21, "Rosa Maduda", "rosa@empresa.com")],
    )

    result = consultar_conversas.invoke({"pessoa": "Rosa"}, config=tool_config())

    assert result["status"] == "sem_dados"
    assert result["total"] == 0
    assert result["mensagens"] == []


def test_conversation_marks_naive_mongo_datetime_as_utc(monkeypatch):
    _, collection = configure_databases(
        monkeypatch, [(7, 21, "Rosa Maduda", "rosa@empresa.com")],
    )
    collection.insert_one({
        "_id": "1", "id_envia": 21, "id_recebe": 7,
        "mensagem": "Oi", "data": datetime(2026, 9, 12, 12, 0),
    })

    result = consultar_conversas.invoke({"pessoa": "Rosa"}, config=tool_config())

    assert result["mensagens"][0]["data"] == "2026-09-12T12:00:00+00:00"


def test_conversation_admin_cannot_query_workspace(monkeypatch):
    monkeypatch.setattr(
        router_tools, "get_postgres_connection",
        lambda: pytest.fail("admin nao deve abrir conexao"),
    )
    result = consultar_conversas.invoke(
        {"pessoa": "Rosa"},
        config={"configurable": {"usuario_atual": {"uid": "admin", "role": "ADMIN"}}},
    )
    assert result["status"] == "nao_aplicavel"


def test_notification_tool_never_accepts_a_user_id():
    properties = consultar_notificacoes.args_schema.model_json_schema()["properties"]
    assert set(properties) == {"pagina", "limite"}


def test_notifications_use_authenticated_postgres_id_and_latest_creation_first(monkeypatch):
    connection, _ = configure_databases(monkeypatch, [(7,)])
    collection = FakeNotificationsCollection()
    monkeypatch.setattr(router_tools, "get_notifications_collection", lambda: collection)
    for identifier, user_id, hour, text in [
        ("old", 7, 8, "Primeira notificação"),
        ("other", 99, 12, "Notificação privada de outra pessoa"),
        ("new", 7, 10, "Segunda notificação"),
    ]:
        collection.insert_one({
            "_id": identifier, "id_usuario": user_id, "mensagem": text,
            "data_criacao": datetime(2026, 9, 13, hour, tzinfo=timezone.utc),
        })

    result = consultar_notificacoes.invoke({}, config=tool_config())

    assert result["status"] == "ok"
    assert result["total"] == 2
    assert [item["mensagem"] for item in result["notificacoes"]] == [
        "Segunda notificação", "Primeira notificação",
    ]
    assert "privada" not in str(result)
    assert "id_usuario" not in str(result)
    assert connection.db_cursor.parameters == ["firebase-sender"]
    assert "FROM usuario WHERE firebase_uid = %s" in connection.db_cursor.query


def test_notifications_are_paginated_and_mark_long_excerpts(monkeypatch):
    configure_databases(monkeypatch, [(7,)])
    collection = FakeNotificationsCollection()
    monkeypatch.setattr(router_tools, "get_notifications_collection", lambda: collection)
    for index in range(4):
        collection.insert_one({
            "_id": str(index), "id_usuario": 7,
            "mensagem": "x" * 600 if index == 1 else f"Notificação {index}",
            "data_criacao": datetime(2026, 9, 13, index, tzinfo=timezone.utc),
        })

    result = consultar_notificacoes.invoke(
        {"pagina": 2, "limite": 2}, config=tool_config(),
    )

    assert result["total_paginas"] == 2
    assert [item["mensagem"] for item in result["notificacoes"]] == [
        "x" * 500, "Notificação 0",
    ]
    assert result["notificacoes"][0]["trecho"] is True


def test_notification_empty_history_and_missing_profile(monkeypatch):
    configure_databases(monkeypatch, [(7,)])
    collection = FakeNotificationsCollection()
    monkeypatch.setattr(router_tools, "get_notifications_collection", lambda: collection)

    result = consultar_notificacoes.invoke({}, config=tool_config())

    assert result["status"] == "sem_dados"
    assert result["notificacoes"] == []

    configure_databases(monkeypatch, [])
    monkeypatch.setattr(
        router_tools, "get_notifications_collection",
        lambda: pytest.fail("sem perfil nao deve consultar Mongo"),
    )
    missing = consultar_notificacoes.invoke({}, config=tool_config())
    assert missing["status"] == "sem_perfil"


def test_notifications_interpret_naive_mongo_timestamp_as_utc(monkeypatch):
    configure_databases(monkeypatch, [(7,)])
    collection = FakeNotificationsCollection()
    monkeypatch.setattr(router_tools, "get_notifications_collection", lambda: collection)
    collection.insert_one({
        "_id": "one", "id_usuario": 7, "mensagem": "Olá",
        "data_criacao": datetime(2026, 9, 13, 12),
    })

    result = consultar_notificacoes.invoke({}, config=tool_config())

    assert result["notificacoes"][0]["data_criacao"] == "2026-09-13T12:00:00+00:00"
