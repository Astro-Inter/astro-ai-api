import pytest
from pydantic import ValidationError
from app.modules.chat.schemas import BlocoGraficoRosca, SessionMessage


def test_donut_roundtrip_with_zero_values():
    graph=BlocoGraficoRosca(titulo="Distribuição",fonte="Cadastro autorizado",unidade="pessoas",
                           dados=[{"categoria":"Grupo","valor":0}])
    assert graph.model_dump()=={"tipo":"grafico_rosca","versao":1,"titulo":"Distribuição",
        "fonte":"Cadastro autorizado","unidade":"pessoas","dados":[{"categoria":"Grupo","valor":0}]}
    message=SessionMessage(role="assistant",content="Resumo",blocos=[graph])
    assert SessionMessage.model_validate_json(message.model_dump_json())==message


@pytest.mark.parametrize("data", [[], [{"categoria":"Grupo","valor":-1}],
    [{"categoria":str(i),"valor":1} for i in range(13)]])
def test_donut_uses_existing_category_limits(data):
    with pytest.raises(ValidationError):
        BlocoGraficoRosca(titulo="Teste",fonte="Teste",dados=data)


@pytest.mark.parametrize("query", ["Rosca da equipe por perfil", "Donut de pendências por NR", "Donut dos vencimentos por faixa"])
def test_donut_intent_uses_authorized_operational_flow(query):
    from app.modules.operational.tools import is_operational_request
    assert is_operational_request(query)
