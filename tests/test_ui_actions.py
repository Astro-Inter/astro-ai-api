import pytest

from app.modules.chat.formatting import markdown_para_texto_simples
from app.modules.chat.subgraphs import _formatar_eventos_google, _formatar_criacao_evento_google
from app.modules.chat.ui_actions import GOOGLE_CALENDAR_CONNECT_ACTION, normalizar_acoes_interface


@pytest.mark.parametrize("value", [
    "/integracoes/google-calendar/status",
    "`/integracoes/google-calendar/conectar`",
    "https://astro.example/integracoes/google-calendar/conectar",
    "[Conectar](https://astro.example/integracoes/google-calendar/conectar)",
    "[Status](/integracoes/google-calendar/status)",
    "[google-calendar-conectar](Conectar)",
    "[google-calendar-conectar](https://outra.example)",
])
def test_connection_actions_are_canonical_and_do_not_contain_endpoint_urls(value):
    text = normalizar_acoes_interface(f"Use esta opção: {value}")
    assert text == f"Use esta opção: {GOOGLE_CALENDAR_CONNECT_ACTION}"
    assert normalizar_acoes_interface(text) == text


@pytest.mark.parametrize("value", [
    "https://astro.example/integracoes/google-calendar/callback",
    "/integracoes/google-calendar/status-interno",
    "/integracoes/google-calendar/conectar/outra-rota",
    "[Baixar PDF](https://r2.example/arquivo.pdf?assinatura=teste)",
    "O Google Calendar já está conectado.",
])
def test_normalization_does_not_change_unrelated_content(value):
    assert normalizar_acoes_interface(value) == value


@pytest.mark.parametrize("marker", [GOOGLE_CALENDAR_CONNECT_ACTION, "[google-calendar-conectar](Conectar)"])
def test_action_marker_survives_plain_text_conversion(marker):
    text = markdown_para_texto_simples(f"**Conecte sua conta.**\n\n{marker}")
    assert text == f"Conecte sua conta.\n\n{marker}"


@pytest.mark.parametrize("formatter", [_formatar_eventos_google, _formatar_criacao_evento_google])
def test_disconnected_calendar_uses_frontend_action_and_not_raw_endpoint(formatter):
    result = {"status": "conexao_necessaria", "rota_conexao": "/integracoes/google-calendar/conectar",
              "evento": {"titulo": "Reunião", "inicio": "2026-10-05T10:00", "fim": "2026-10-05T11:00"}}
    text = formatter(result)
    assert GOOGLE_CALENDAR_CONNECT_ACTION in text
    assert "/integracoes/google-calendar/" not in text


def test_authenticated_action_does_not_replace_normal_markdown_links():
    assert markdown_para_texto_simples("[Fonte](https://astro.example)") == "Fonte (https://astro.example)"
