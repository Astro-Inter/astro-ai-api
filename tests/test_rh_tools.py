import psycopg
import pytest
from pydantic import ValidationError

from app.core import config
from app.modules.rh import tools as rh_tools
from app.modules.rh.tools import (
    BuscarOutrosUsuariosArgs,
    RhToolDecision,
    buscar_meus_dados,
    buscar_outros_usuarios,
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


def run_search(monkeypatch, role, filters, rows=None, uid="firebase-owner"):
    rows = rows if rows is not None else [(
        "Ana Lima", "ana@example.com", "FUNCIONARIO", "Soldador", "Matriz",
        "PRESENCIAL", "ATIVO",
    )]
    connection = FakeConnection(rows)
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test:test@localhost/astro")
    monkeypatch.setattr(rh_tools, "get_conn", lambda: connection)
    result = buscar_outros_usuarios.invoke(
        filters.model_dump(),
        config={"configurable": {"usuario_atual": {"uid": uid, "role": role}}},
    )
    return result, connection


def test_tools_follow_langchain_pattern_and_hide_authenticated_context():
    assert buscar_outros_usuarios.name == "buscar_outros_usuarios"
    assert buscar_meus_dados.name == "buscar_meus_dados"
    assert rh_tools.TOOLS_RH == [buscar_outros_usuarios, buscar_meus_dados]
    for registered_tool in rh_tools.TOOLS_RH:
        schema = registered_tool.args_schema.model_json_schema()["properties"]
        assert "config" not in schema
        assert "uid" not in schema and "role" not in schema
    assert buscar_meus_dados.args_schema.model_json_schema()["properties"] == {}


@pytest.mark.parametrize("filters", [
    {},
    {"status": [], "tipos": [], "nome": None, "cargo": None, "limite": 20},
])
def test_current_user_decision_accepts_provider_generated_empty_filters(filters):
    decision = RhToolDecision.model_validate({
        "acao": "buscar_meus_dados", "filtros": filters, "resposta": None,
    })
    assert decision.filtros is None


def test_current_user_decision_rejects_real_filters():
    with pytest.raises(ValidationError):
        RhToolDecision.model_validate({
            "acao": "buscar_meus_dados",
            "filtros": {"nome": "Outra pessoa"},
            "resposta": None,
        })


@pytest.mark.parametrize("payload", [
    {"acao": "buscar_outros_usuarios", "filtros": None, "resposta": None},
    {"acao": "buscar_outros_usuarios", "resposta": None},
])
def test_other_users_decision_treats_missing_filters_as_search_all(payload):
    decision = RhToolDecision.model_validate(payload)
    assert decision.filtros == BuscarOutrosUsuariosArgs()


def test_manager_search_is_limited_to_own_unit_and_allowed_profiles(monkeypatch):
    filters = BuscarOutrosUsuariosArgs(
        status=["ATIVO", "PRE_CADASTRADO"],
        tipos=["FUNCIONARIO"],
        nome="Ana%_",
        cargo="soldador",
        limite=15,
    )
    result, connection = run_search(monkeypatch, "GESTOR", filters)

    query = connection.db_cursor.query
    assert "FROM usuario" in query
    assert "JOIN cargo" in query
    assert "JOIN unidade" in query
    assert "usuario.unidade_id = (" in query
    assert "unidade.workspace_id = (" not in query
    assert "WHERE firebase_uid = %s" in query
    assert "usuario.status = ANY(%s)" in query
    assert "usuario.tipo = ANY(%s)" in query
    assert "usuario.nome ILIKE %s" in query
    assert "cargo.nome ILIKE %s" in query
    assert connection.db_cursor.parameters == [
        "firebase-owner", ["GESTOR", "FUNCIONARIO"], "firebase-owner",
        ["ATIVO", "PRE_CADASTRADO"], ["FUNCIONARIO"],
        r"%Ana\%\_%", "%soldador%", 15,
    ]
    assert result == {
        "status": "ok",
        "quantidade": 1,
        "usuarios": [{
            "nome": "Ana Lima", "email": "ana@example.com", "tipo": "FUNCIONARIO",
            "cargo": "Soldador", "unidade": "Matriz", "modalidade": "PRESENCIAL",
            "status": "ATIVO",
        }],
    }


def test_workspace_manager_search_is_limited_to_workspace_and_allowed_profiles(monkeypatch):
    result, connection = run_search(
        monkeypatch, "GESTOR_WORKSPACE", BuscarOutrosUsuariosArgs(), rows=[],
    )
    query = connection.db_cursor.query
    assert "unidade.workspace_id = (" in query
    assert "unidade_atual.workspace_id" in query
    assert "usuario.unidade_id = (" not in query
    assert connection.db_cursor.parameters == [
        "firebase-owner", ["GESTOR", "GESTOR_WORKSPACE", "FUNCIONARIO"],
        "firebase-owner", 20,
    ]
    assert result == {"status": "sem_dados", "quantidade": 0, "usuarios": []}


def test_employee_cannot_search_other_users_or_open_database(monkeypatch):
    monkeypatch.setattr(
        rh_tools, "get_conn",
        lambda: pytest.fail("A conexão não deveria ser aberta para FUNCIONARIO."),
    )
    result = buscar_outros_usuarios.invoke(
        {},
        config={"configurable": {
            "usuario_atual": {"uid": "employee", "role": "FUNCIONARIO"},
        }},
    )
    assert result == {
        "status": "nao_autorizado",
        "mensagem": "Seu perfil nao permite consultar outros usuarios.",
    }


def test_admin_can_search_all_units_but_never_returns_itself(monkeypatch):
    result, connection = run_search(
        monkeypatch, "ADMIN", BuscarOutrosUsuariosArgs(),
        rows=[], uid="firebase-admin",
    )
    query = connection.db_cursor.query
    assert "usuario.unidade_id = (" not in query
    assert "usuario.firebase_uid <> %s" in query
    assert connection.db_cursor.parameters == ["firebase-admin", 20]
    assert result == {"status": "sem_dados", "quantidade": 0, "usuarios": []}


def test_current_user_tool_returns_only_authenticated_user(monkeypatch):
    from datetime import datetime

    row = (
        "Lucas Lima", "lucas@example.com", "12345678901", "FUNCIONARIO",
        "Analista", "Matriz", "HIBRIDO", "ATIVO", datetime(2026, 9, 10, 10, 30),
    )
    connection = FakeConnection([row])
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test:test@localhost/astro")
    monkeypatch.setattr(rh_tools, "get_conn", lambda: connection)

    result = buscar_meus_dados.invoke(
        {},
        config={"configurable": {
            "usuario_atual": {"uid": "firebase-owner", "role": "FUNCIONARIO"},
        }},
    )

    assert "FROM usuario" in connection.db_cursor.query
    assert "JOIN cargo" in connection.db_cursor.query
    assert "JOIN unidade" in connection.db_cursor.query
    assert "usuario.firebase_uid = %s" in connection.db_cursor.query
    assert connection.db_cursor.parameters == ["firebase-owner"]
    assert result["status"] == "ok"
    assert result["dados"]["cpf"] == "12345678901"
    assert result["dados"]["cargo"] == "Analista"


def test_current_user_tool_returns_sem_dados(monkeypatch):
    connection = FakeConnection([])
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test:test@localhost/astro")
    monkeypatch.setattr(rh_tools, "get_conn", lambda: connection)
    result = buscar_meus_dados.invoke(
        {},
        config={"configurable": {"usuario_atual": {"uid": "missing", "role": "FUNCIONARIO"}}},
    )
    assert result == {"status": "sem_dados", "dados": None}


def test_current_admin_tool_reads_admin_table(monkeypatch):
    connection = FakeConnection([("Admin Astro", "admin@example.com")])
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test:test@localhost/astro")
    monkeypatch.setattr(rh_tools, "get_conn", lambda: connection)
    result = buscar_meus_dados.invoke(
        {},
        config={"configurable": {"usuario_atual": {"uid": "admin-uid", "role": "ADMIN"}}},
    )
    assert "FROM admin" in connection.db_cursor.query
    assert connection.db_cursor.parameters == ["admin-uid"]
    assert result["dados"]["nome"] == "Admin Astro"
    assert result["dados"]["tipo"] == "ADMIN"
    assert result["dados"]["cargo"] is None


@pytest.mark.parametrize("payload", [
    {"status": ["BLOQUEADO"]},
    {"tipos": ["ADMIN"]},
    {"status": ["ATIVO", "ATIVO"]},
    {"limite": 51},
    {"campo_sql": "DROP TABLE usuarios"},
])
def test_search_filters_reject_invalid_values(payload):
    with pytest.raises(ValidationError):
        BuscarOutrosUsuariosArgs.model_validate(payload)


def test_missing_authenticated_context_does_not_open_database(monkeypatch):
    monkeypatch.setattr(
        rh_tools, "get_conn",
        lambda: pytest.fail("A conexão não deveria ser aberta sem usuário."),
    )
    assert buscar_outros_usuarios.invoke({}) == {
        "status": "erro", "mensagem": "Usuario nao identificado no contexto.",
    }


def test_database_failure_returns_safe_error(monkeypatch):
    def connect():
        raise psycopg.OperationalError("postgresql://user:secret@private-host/astro")

    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://user:secret@private-host/astro")
    monkeypatch.setattr(rh_tools, "get_conn", connect)
    result = buscar_outros_usuarios.invoke(
        {},
        config={"configurable": {
            "usuario_atual": {"uid": "owner", "role": "ADMIN"},
        }},
    )
    assert result == {
        "status": "indisponivel", "mensagem": "Consulta de usuarios indisponivel.",
    }
    assert "secret" not in str(result)
