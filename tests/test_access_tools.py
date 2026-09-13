from datetime import date

import pytest
from pydantic import ValidationError

from app.core import config
from app.modules.roteador import tools as router_tools
from app.modules.roteador.tools import ConsultarAcessosArgs, consultar_acessos


class FakeCursor:
    def __init__(self, *, user_id=7, days=()):
        self.user_id = user_id
        self.days = sorted(days, reverse=True)
        self.calls = []
        self.query = ""
        self.params = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def execute(self, query, params):
        self.query = query
        self.params = params
        self.calls.append((query, params))

    def fetchone(self):
        if "FROM usuario" in self.query:
            return (self.user_id,) if self.user_id is not None else None
        return len(self.days), min(self.days, default=None), max(self.days, default=None)

    def fetchall(self):
        return [(day,) for day in self.days[self.params[-1]:self.params[-1] + self.params[-2]]]


class FakeConnection:
    def __init__(self, cursor):
        self.db_cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def cursor(self):
        return self.db_cursor


def setup_db(monkeypatch, *, user_id=7, days=()):
    cursor = FakeCursor(user_id=user_id, days=days)
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test/test")
    monkeypatch.setattr(router_tools, "get_postgres_connection", lambda: FakeConnection(cursor))
    return cursor


def auth_config(uid="firebase-self"):
    return {"configurable": {"usuario_atual": {"uid": uid, "role": "FUNCIONARIO"}}}


def test_schema_does_not_accept_target_user_and_validates_period():
    properties = consultar_acessos.args_schema.model_json_schema()["properties"]
    assert "uid" not in properties
    assert "usuario_id" not in properties
    with pytest.raises(ValidationError):
        ConsultarAcessosArgs(periodo="mes_especifico", ano=2026)
    with pytest.raises(ValidationError):
        ConsultarAcessosArgs(
            periodo="intervalo", data_inicio=date(2026, 9, 10),
            data_fim=date(2026, 9, 1),
        )


def test_count_uses_authenticated_uid_and_current_month_in_database(monkeypatch):
    cursor = setup_db(monkeypatch, days=[date(2026, 9, 1), date(2026, 9, 12)])
    result = consultar_acessos.invoke(
        {"consulta": "contagem", "periodo": "mes_atual"}, config=auth_config(),
    )
    assert result["total_dias_com_acesso"] == 2
    assert result["primeiro_dia_registrado"] == "2026-09-01"
    assert result["ultimo_dia_registrado"] == "2026-09-12"
    assert result["dias"] == []
    assert cursor.calls[0][1] == ["firebase-self"]
    assert cursor.calls[1][1] == [7]
    assert "acesso.usuario_id = %s" in cursor.calls[1][0]
    assert "CURRENT_DATE" in cursor.calls[1][0]


def test_first_access_uses_all_history_without_inferring_login_time(monkeypatch):
    setup_db(monkeypatch, days=[date(2024, 1, 9), date(2026, 9, 12)])
    result = consultar_acessos.invoke({"consulta": "primeiro"}, config=auth_config())
    assert result["primeiro_dia_registrado"] == "2024-01-09"
    assert "nao contabiliza logins individuais nem horarios" in result["granularidade"]


def test_explanation_does_not_need_database(monkeypatch):
    monkeypatch.setattr(router_tools, "get_postgres_connection", lambda: 1 / 0)
    result = consultar_acessos.invoke(
        {"consulta": "explicacao"}, config=auth_config(),
    )
    assert result["status"] == "ok"
    assert result["consulta"] == "explicacao"


def test_specific_month_and_year_are_bounded_date_ranges(monkeypatch):
    cursor = setup_db(monkeypatch, days=[date(2026, 9, 12)])
    consultar_acessos.invoke(
        {"periodo": "mes_especifico", "ano": 2026, "mes": 9}, config=auth_config(),
    )
    assert cursor.calls[1][1] == [7, date(2026, 9, 1), date(2026, 10, 1)]
    cursor.calls.clear()
    consultar_acessos.invoke(
        {"periodo": "ano_especifico", "ano": 2026}, config=auth_config(),
    )
    assert cursor.calls[1][1] == [7, date(2026, 1, 1), date(2027, 1, 1)]


def test_explicit_interval_and_days_pagination(monkeypatch):
    cursor = setup_db(
        monkeypatch,
        days=[date(2026, 9, day) for day in range(1, 13)],
    )
    result = consultar_acessos.invoke(
        {
            "consulta": "dias", "periodo": "intervalo",
            "data_inicio": "2026-09-01", "data_fim": "2026-09-30",
            "pagina": 2, "limite": 5,
        },
        config=auth_config(),
    )
    assert result["total_dias_com_acesso"] == 12
    assert result["total_paginas"] == 3
    assert result["dias"] == [f"2026-09-{day:02d}" for day in range(7, 2, -1)]
    assert cursor.calls[1][1] == [7, date(2026, 9, 1), date(2026, 9, 30)]
    assert cursor.calls[2][1] == [7, date(2026, 9, 1), date(2026, 9, 30), 5, 5]


def test_missing_profile_or_database_do_not_leak_other_users(monkeypatch):
    cursor = setup_db(monkeypatch, user_id=None)
    result = consultar_acessos.invoke({}, config=auth_config())
    assert result["status"] == "sem_perfil"
    assert len(cursor.calls) == 1

    monkeypatch.setattr(router_tools, "get_postgres_connection", lambda: 1 / 0)
    result = consultar_acessos.invoke({}, config=auth_config())
    assert result["status"] == "indisponivel"
