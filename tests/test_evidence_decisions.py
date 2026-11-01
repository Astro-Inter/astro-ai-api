import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.core import config
from app.core.security import CurrentUser
from app.infrastructure.llm import decisions
from app.modules.chat.schemas import ChatRequest
from app.modules.chat.service import ChatService
from memory_fakes import FakeSessions, FakeVectors, FakeFaqVectors
from test_chat import FakeModel


@pytest.mark.parametrize("supported,review_status,expected", [
    (True, "aprovado", True), (False, "aprovado", True), (None, "aprovado", True),
    (False, "rejeitado", False), (None, "revisar", False),
])
def test_final_gate_escalates_jev_rejection_to_textual_judge(monkeypatch, supported, review_status, expected):
    monkeypatch.setattr(config, "OPENROUTER_EVIDENCE_CHECK_ENABLED", True)
    model = FakeModel("faq")
    model.verify_evidence = AsyncMock(return_value=supported)
    if supported is not True:
        model.replies["juiz"] = [json.dumps({"status": "aprovado", "motivo": "Fontes confirmadas.", "problemas": []}),
                                json.dumps({"status": review_status, "motivo": "Revisão do texto final.", "problemas": [] if review_status=="aprovado" else ["Sem comprovação."]})]
    repo = FakeSessions()
    service = ChatService(model, repository=repo, vectors=FakeVectors(), faq_vectors=FakeFaqVectors())
    async def scenario():
        response = await service.chat(ChatRequest(message="Qual o objetivo da plataforma?"), CurrentUser(uid="owner", role="COLABORADOR"))
        text = "\n\n".join(block.conteudo for block in response.blocos if block.tipo == "texto")
        if expected:
            assert "Fonte: normas.pdf" in text
        else:
            assert text == "Não tenho evidência para afirmar isso."
            assert not repo.docs[str(response.session_id)]["mensagens"]
        sent = model.verify_evidence.await_args.args[0]
        assert sent["resposta"] and "trechos" in sent["resultado"]
        assert [name for name, _, _ in model.calls].count("juiz") == (1 if supported is True else 2)
    asyncio.run(scenario())


@pytest.mark.parametrize("verdict,noul,confidence,expected", [
    ("apoiada", 0.9, 0.95, True), ("sem_apoio", 0.1, 0.95, False),
    ("parcial", 0.5, 0.9, None), ("apoiada", 0.69, 0.95, None),
    ("apoiada", 0.9, 0.69, None), ("sem_apoio", 0.95, 0.99, None),
])
def test_choice_and_noul_check_current_sources_without_generating_text(monkeypatch, verdict, noul, confidence, expected):
    request = AsyncMock(return_value={"answers": {
        "veredito": {"type": "choice", "choice": verdict, "confidence": confidence,
                     "probabilities": {k: 1 if k == verdict else 0 for k in ("apoiada", "parcial", "sem_apoio")}},
        "apoiada": {"type": "noul", "noul": noul},
    }})
    monkeypatch.setattr(decisions, "request", request)
    state = {"mensagem": "Qual o contato?", "resposta": "Fale com ana@privado.example", "resultado": {
        "resposta": "Nao pode sustentar a si mesma", "trechos": [{"conteudo": "Contato ana@privado.example"}]}}
    assert asyncio.run(decisions.verify_evidence(state)) is expected
    payload = request.await_args.args[1]
    assert "Nao pode sustentar a si mesma" not in json.dumps(payload)
    assert "ana@privado.example" not in json.dumps(payload)
    assert payload["model"] == "typesafe/jev-1.14"
    assert set(payload["questions"]) == {"veredito", "apoiada"}
