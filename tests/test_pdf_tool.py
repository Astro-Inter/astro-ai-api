from io import BytesIO

from pypdf import PdfReader

from app.core import config
from app.modules.shared import tools as pdf_tools


def auth_config():
    return {"configurable": {"usuario_atual": {"uid": "firebase-self", "role": "COLABORADOR"}}}


def test_dynamic_pdf_contains_question_answer_sources_and_multiple_pages():
    answer = (
        "**Conclusão:** a norma exige cuidado com a máquina.\n"
        "- Treinamento registrado\n"
        "- Fonte: NR-12, seção 2\n"
        + ("Detalhes adicionais sobre segurança do trabalho.\n" * 100)
    )
    content = pdf_tools.renderizar_pdf(
        titulo="Consulta de SST - NR-12",
        pergunta="Quais são as exigências da NR-12?",
        resposta=answer,
    )
    reader = PdfReader(BytesIO(content))
    text = "\n".join(page.extract_text() for page in reader.pages)

    assert content.startswith(b"%PDF-")
    assert len(reader.pages) >= 2
    assert "Consulta de SST - NR-12" in text
    assert "Quais são as exigências da NR-12?" in text
    assert "Treinamento registrado" in text
    assert "Fonte: NR-12, seção 2" in text
    assert "Página 1" in reader.pages[0].extract_text()
    for page in reader.pages:
        resources = page["/Resources"]
        font_names = {
            str(font.get_object().get("/BaseFont"))
            for font in resources["/Font"].values()
        }
        assert any("MuseoModerno-Medium" in name for name in font_names)
        assert any("MuseoModerno-Bold" in name for name in font_names)
        assert len(resources["/XObject"]) >= 1


def test_pdf_tool_uploads_only_generated_bytes_and_returns_signed_url(monkeypatch):
    class FakeR2:
        def __init__(self):
            self.signed = None
            self.upload = None

        def generate_presigned_url(self, operation, *, Params, ExpiresIn):
            self.signed = (operation, Params, ExpiresIn)
            return "https://r2.example/arquivo.pdf?assinatura=teste"

        def put_object(self, **kwargs):
            self.upload = kwargs

    fake = FakeR2()
    monkeypatch.setattr(config, "R2_ACCESS_KEY_ID", "fake-access")
    monkeypatch.setattr(config, "R2_SECRET_ACCESS_KEY", "fake-secret")
    monkeypatch.setattr(config, "R2_ENDPOINT", "https://example.r2.cloudflarestorage.com")
    monkeypatch.setattr(config, "R2_BUCKET_NAME", "astro-ai-files")
    monkeypatch.setattr(pdf_tools, "get_r2_client", lambda: fake)

    result = pdf_tools.gerar_pdf.invoke(
        {
            "titulo": "Relatório de RH", "pergunta": "Quais meus dados?",
            "resposta": "Nome: Ana. Cargo: Soldadora.",
        },
        config=auth_config(),
    )

    assert result == {
        "status": "ok",
        "url": "https://r2.example/arquivo.pdf?assinatura=teste",
        "expira_em_segundos": 18000,
    }
    assert fake.signed[0] == "get_object"
    assert fake.signed[1]["Bucket"] == "astro-ai-files"
    assert fake.signed[1]["Key"].startswith("consultas/")
    assert fake.signed[1]["Key"].endswith(".pdf")
    assert fake.signed[2] == 18000
    assert fake.upload["Bucket"] == "astro-ai-files"
    assert fake.upload["Key"] == fake.signed[1]["Key"]
    assert fake.upload["ContentType"] == "application/pdf"
    assert fake.upload["ContentDisposition"] == (
        'attachment; filename="relatorio-de-rh.pdf"'
    )
    assert fake.upload["Body"].startswith(b"%PDF-")
    assert "Ana" in PdfReader(BytesIO(fake.upload["Body"])).pages[0].extract_text()


def test_pdf_tool_does_not_upload_without_user_or_configuration(monkeypatch):
    monkeypatch.setattr(config, "R2_BUCKET_NAME", "")
    monkeypatch.setattr(pdf_tools, "get_r2_client", lambda: 1 / 0)
    args = {"titulo": "Teste", "pergunta": "Pergunta", "resposta": "Resposta"}

    assert pdf_tools.gerar_pdf.invoke(args)["status"] == "erro"
    assert pdf_tools.gerar_pdf.invoke(args, config=auth_config())["status"] == "indisponivel"

    monkeypatch.setattr(config, "R2_ACCESS_KEY_ID", "fake-access")
    monkeypatch.setattr(config, "R2_SECRET_ACCESS_KEY", "fake-secret")
    monkeypatch.setattr(config, "R2_BUCKET_NAME", "astro-ai-files")
    monkeypatch.setattr(config, "R2_ENDPOINT", "http://example.r2.cloudflarestorage.com")
    assert pdf_tools.gerar_pdf.invoke(args, config=auth_config())["status"] == "indisponivel"


def test_pdf_tool_reports_upload_failure_without_leaking_signed_url(monkeypatch, caplog):
    class FailingR2:
        def generate_presigned_url(self, *_args, **_kwargs):
            return "https://r2.example/segredo?assinatura=privada"

        def put_object(self, **_kwargs):
            raise RuntimeError("Falha contendo https://r2.example/segredo?assinatura=privada")

    monkeypatch.setattr(config, "R2_ACCESS_KEY_ID", "fake-access")
    monkeypatch.setattr(config, "R2_SECRET_ACCESS_KEY", "fake-secret")
    monkeypatch.setattr(config, "R2_ENDPOINT", "https://example.r2.cloudflarestorage.com")
    monkeypatch.setattr(config, "R2_BUCKET_NAME", "astro-ai-files")
    monkeypatch.setattr(pdf_tools, "get_r2_client", FailingR2)

    result = pdf_tools.gerar_pdf.invoke(
        {"titulo": "Teste", "pergunta": "Pergunta", "resposta": "Resposta"},
        config=auth_config(),
    )
    assert result == {"status": "indisponivel", "mensagem": "Geracao de PDF indisponivel."}
    assert "assinatura=privada" not in caplog.text
    assert "etapa=upload" in caplog.text
