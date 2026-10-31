import pytest
from pydantic import ValidationError

from app.modules.chat.schemas import BlocoTabela


def table():
    return {"versao": 2, "titulo": "Usuários", "colunas": [{"chave": "nome", "titulo": "Nome"}],
            "linhas": [{"nome": f"Pessoa {i}"} for i in range(100)],
            "pagina": 1, "total_paginas": 1, "total": 143, "solicitada": 100,
            "retornada": 100, "limitado": False, "tem_mais": True, "paginacao": "local"}


def test_local_table_roundtrips_for_history():
    original = BlocoTabela.model_validate(table())
    restored = BlocoTabela.model_validate_json(original.model_dump_json())
    assert restored == original
    assert len(restored.linhas) == 100


@pytest.mark.parametrize("field,value", [("retornada", 99), ("total", 90),
    ("tem_mais", False), ("limitado", True), ("solicitada", 20)])
def test_local_table_rejects_misleading_metadata(field, value):
    data = table()
    data[field] = value
    with pytest.raises(ValidationError):
        BlocoTabela.model_validate(data)


def test_legacy_table_still_has_fifty_row_limit():
    data = table()
    data["versao"] = 1
    with pytest.raises(ValidationError):
        BlocoTabela.model_validate(data)
