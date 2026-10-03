import json

import pytest

from app.modules.chat.router_reply import normalize_router_reply


@pytest.mark.parametrize("reply", [
    "ROUTE=agenda\n\nQual é o título da reunião?",
    "route = AGENDA\r\nVou encaminhar a pergunta.",
    "```text\nROUTE=agenda\nQual data?\n```",
    "\ufeffROUTE=agenda",
])
def test_single_route_tolerates_benign_formatting(reply):
    assert normalize_router_reply(reply) == "ROUTE=agenda"


@pytest.mark.parametrize("reply", [
    "ROUTE=agenda\nROUTE=rh", "ROUTE=agenda\nMESSAGE={}",
    "ROUTE=desconhecido", "Vou encaminhar: ROUTE=rh", "ROUTE agenda",
    'MESSAGE={"destinatario":"Rosa","mensagem":"oi","uid":"outro"}',
    'MESSAGE={"destinatario":"Rosa","mensagem":"oi"}\nEnviado!',
    'NOTIFICATIONS={"limite":999}', "",
])
def test_ambiguous_or_unauthorized_tool_arguments_are_not_repaired(reply):
    with pytest.raises(ValueError):
        normalize_router_reply(reply)


def test_format_normalization_never_confirms_a_message():
    reply = normalize_router_reply(
        'message = {"destinatario":"Rosa","mensagem":"oi","confirmar_envio":true}'
    )
    assert json.loads(reply.split("=", 1)[1])["confirmar_envio"] is False


def test_natural_clarification_is_preserved():
    assert normalize_router_reply("Qual agenda pretende usar?") == "Qual agenda pretende usar?"
