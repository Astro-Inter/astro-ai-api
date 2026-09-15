from datetime import date, datetime
import pytest
from pydantic import ValidationError

from app.core import config
from app.modules.agenda import tools as agenda_tools
from app.modules.agenda.tools import consultar_treinamentos
from app.modules.agenda.tools import consultar_eventos, ConsultarEventosArgs, AgendaToolDecision


class FakeCursor:
    def __init__(self, profile=(7,), total=1, rows=None):
        self.profile = profile
        self.total = total
        self.rows = rows or []
        self.calls = []
        self.last_query = ""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def execute(self, query, params):
        self.last_query = query
        self.calls.append((query, params))

    def fetchone(self):
        if "SELECT id_usuario FROM usuario" in self.last_query:
            return self.profile
        return (self.total,)

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, cursor):
        self.db_cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def cursor(self):
        return self.db_cursor


def tool_config(role="FUNCIONARIO"):
    return {"configurable": {"usuario_atual": {"uid": "firebase-user", "role": role}}}


def setup_database(monkeypatch, cursor):
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test:test@localhost/astro")
    monkeypatch.setattr(
        agenda_tools, "get_postgres_connection", lambda: FakeConnection(cursor),
    )


def test_internal_events_use_user_assignments_and_future_active_dates(monkeypatch):
    cursor = FakeCursor(rows=[(
        "Evento futuro", "Descrição", None, "GESTOR", False, "ATIVO", "Turma A",
        datetime(2027, 9, 20, 8), datetime(2027, 9, 20, 12),
        None, None, "CONCLUIDO", None, None, None, None,
    )])
    setup_database(monkeypatch, cursor)
    result = consultar_eventos.invoke({"limite": 1}, config=tool_config())
    assert result["eventos"][0]["titulo"] == "Evento futuro"
    assert "treinamentos" not in result
    assert cursor.calls[0][1] == ["firebase-user"]
    assert cursor.calls[2][1] == [7, 1, 0]
    for query, _ in cursor.calls[1:]:
        assert "participacao.usuario_id = %s" in query
        assert "evento.status = 'ATIVO'" in query
        assert "turma.data_inicial >=" in query
        assert "AT TIME ZONE 'America/Sao_Paulo'" in query
        assert "IN ('PENDENTE', 'REJEITADO')" not in query
    assert "ORDER BY turma.data_inicial, turma.id_turma" in cursor.calls[2][0]


def test_internal_events_history_and_missing_data(monkeypatch):
    cursor = FakeCursor(total=0)
    setup_database(monkeypatch, cursor)
    result = consultar_eventos.invoke({"proximos": False}, config=tool_config())
    assert result["status"] == "sem_dados"
    assert result["eventos"] == []
    assert "turma.data_inicial >=" not in cursor.calls[1][0]


def test_internal_events_filters_do_not_accept_another_user():
    with pytest.raises(ValidationError):
        ConsultarEventosArgs(uid="another-user")
    assert isinstance(AgendaToolDecision(acao="consultar_eventos").filtros, ConsultarEventosArgs)


def test_training_tool_accepts_only_status_and_pagination():
    properties = consultar_treinamentos.args_schema.model_json_schema()["properties"]
    assert set(properties) == {"situacao", "pagina", "limite"}


def test_pending_training_is_scoped_to_authenticated_user_and_returns_schedule(monkeypatch):
    cursor = FakeCursor(rows=[(
        "Operação segura de máquinas", "Capacitação prática", "https://astro.test/nr12",
        "GESTOR", True, "ATIVO", "Turma A",
        datetime(2026, 9, 20, 8), datetime(2026, 9, 20, 12),
        12, "Máquinas e Equipamentos", "PENDENTE", None, None, None, None,
    )])
    setup_database(monkeypatch, cursor)

    result = consultar_treinamentos.invoke({}, config=tool_config())

    assert result["status"] == "ok"
    assert result["situacao"] == "a_realizar"
    assert result["total"] == 1
    assert result["treinamentos"][0] == {
        "titulo": "Operação segura de máquinas",
        "descricao": "Capacitação prática",
        "link_externo": "https://astro.test/nr12",
        "modo_conclusao": "GESTOR",
        "evidencia_obrigatoria": True,
        "status_evento": "ATIVO",
        "turma": "Turma A",
        "data_inicio": "2026-09-20T08:00:00",
        "data_termino": "2026-09-20T12:00:00",
        "nr": {"numero": 12, "titulo": "Máquinas e Equipamentos"},
        "status_participacao": "PENDENTE",
        "data_conclusao": None,
        "data_validade": None,
        "data_validacao": None,
        "motivo_rejeicao": None,
    }
    assert "firebase-user" not in str(result)
    assert "id_usuario" not in str(result)
    assert cursor.calls[0][1] == ["firebase-user"]
    assert cursor.calls[1][1] == [7]
    assert cursor.calls[2][1] == [7, 5, 0]
    query = cursor.calls[2][0]
    for table in ("turma_funcionario", "turma", "evento", "conclusao_evento"):
        assert table in query
    assert "evento.status = 'ATIVO'" in query
    assert "COALESCE(conclusao.status, 'PENDENTE') IN ('PENDENTE', 'REJEITADO')" in query


def test_training_filters_and_pagination_are_applied_without_user_ids(monkeypatch):
    cursor = FakeCursor(total=7, rows=[(
        "NR-6", "Descrição", None, "FUNCIONARIO", False, "CONCLUIDO", "Turma B",
        datetime(2026, 8, 1, 9), datetime(2026, 8, 1, 12),
        6, "EPI", "CONCLUIDO", datetime(2026, 8, 1, 12), date(2027, 8, 1),
        datetime(2026, 8, 2, 10), None,
    )])
    setup_database(monkeypatch, cursor)

    result = consultar_treinamentos.invoke(
        {"situacao": "concluidos", "pagina": 2, "limite": 3}, config=tool_config(),
    )

    assert result["total_paginas"] == 3
    assert result["treinamentos"][0]["data_validade"] == "2027-08-01"
    assert cursor.calls[2][1] == [7, 3, 3]
    assert "conclusao.status = 'CONCLUIDO'" in cursor.calls[2][0]
    assert "evento.status = 'ATIVO'" not in cursor.calls[2][0]


def test_training_missing_profile_does_not_query_participations(monkeypatch):
    cursor = FakeCursor(profile=None)
    setup_database(monkeypatch, cursor)

    result = consultar_treinamentos.invoke({}, config=tool_config())

    assert result["status"] == "sem_perfil"
    assert len(cursor.calls) == 1


def test_training_empty_result_is_not_reported_as_database_failure(monkeypatch):
    cursor = FakeCursor(total=0)
    setup_database(monkeypatch, cursor)

    result = consultar_treinamentos.invoke({}, config=tool_config())

    assert result["status"] == "sem_dados"
    assert result["treinamentos"] == []
    assert len(cursor.calls) == 2


def test_admin_cannot_query_functional_training(monkeypatch):
    monkeypatch.setattr(
        agenda_tools, "get_postgres_connection",
        lambda: (_ for _ in ()).throw(AssertionError("nao deve acessar o banco")),
    )
    result = consultar_treinamentos.invoke({}, config=tool_config(role="ADMIN"))
    assert result["status"] == "nao_aplicavel"
