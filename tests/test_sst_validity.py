import pytest

from app.modules.chat.subgraphs import _formatar_situacao_nrs


@pytest.mark.parametrize("third_party", [False, True])
def test_missing_validity_date_is_not_evidence_of_compliance(third_party):
    result = {
        "status": "ok", "cargo": "Gestor de Segurança", "unidade": "Matriz",
        "nrs": [{"numero": 1, "titulo": "Disposições gerais",
                 "situacao": "REALIZACAO_NECESSARIA", "acao_necessaria": "REALIZAR",
                 "data_validade": None}],
    }
    if third_party:
        result.update(consulta_terceiro=True, usuario="Pessoa teste")
    text = _formatar_situacao_nrs(result)
    assert "precisa ser realizada" in text
    assert "sem data de validade" in text
    assert "Não é possível confirmar se estão em dia" in text
    assert "|" not in text
    assert "nenhuma está vencida" not in text.lower()


@pytest.mark.parametrize("validity,situation,action,label", [
    ("2027-09-12", "VIGENTE", "NENHUMA", "está em dia"),
    ("2026-08-01", "RENOVACAO_NECESSARIA", "RENOVAR", "precisa ser renovada"),
])
def test_registered_validity_and_situation_are_preserved(validity, situation, action, label):
    result = {
        "status": "ok", "cargo": "Eletricista", "unidade": "Matriz",
        "nrs": [{"numero": 10, "titulo": "Eletricidade",
                 "situacao": situation, "acao_necessaria": action,
                 "data_validade": validity}],
    }
    text = _formatar_situacao_nrs(result)
    assert f"Validade: {validity[8:10]}/{validity[5:7]}/{validity[:4]}" in text
    assert label in text
    assert "validade não registrada" not in text
    assert "Ausência de data" not in text
