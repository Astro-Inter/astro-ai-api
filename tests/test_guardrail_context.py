from app.modules.chat.agents import _history_for_agent
from app.modules.chat.prompts.rh import RH_DECISAO_PROMPT_COMPLETO
from app.modules.chat.prompts.roteador import ROTEADOR_PROMPT
from app.modules.guardrails.entrada import GUARDRAIL_ENTRADA_PROMPT_COMPLETO


def test_input_guardrail_receives_recent_context_for_short_followups():
    history = [
        {"role": "user", "content": "me mostre um funcionario do workspace"},
        {"role": "assistant", "content": "Qual funcionário você gostaria de ver?"},
    ]
    assert _history_for_agent("guardrail_entrada", history) == history


def test_input_guardrail_history_is_bounded():
    history = [{"role": "user", "content": str(i)} for i in range(12)]
    assert _history_for_agent("guardrail_entrada", history) == history[-6:]
    selected = _history_for_agent("guardrail_entrada", [
        {"role": "user", "content": "INICIO " + "x" * 6001 + " FIM"},
    ])
    assert len(selected) == 1
    assert len(selected[0]["content"]) <= 6000
    assert selected[0]["content"].startswith("INICIO") and selected[0]["content"].endswith("FIM")
    assert "trecho omitido" in selected[0]["content"]


def test_rh_prompts_use_trusted_role_without_asking_for_permission():
    for prompt in (
        GUARDRAIL_ENTRADA_PROMPT_COMPLETO,
        ROTEADOR_PROMPT,
        RH_DECISAO_PROMPT_COMPLETO,
    ):
        assert "usuario_atual.role" in prompt
        assert "só um" in prompt
    assert "não autoriza consultar todo o workspace" in GUARDRAIL_ENTRADA_PROMPT_COMPLETO
    assert "sem afirmar seleção aleatória" in RH_DECISAO_PROMPT_COMPLETO
