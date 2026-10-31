import importlib
from pathlib import Path

import pytest

from app.modules.operational.tools import is_operational_request


@pytest.mark.parametrize("question", ["Mostre a equipe em barras por cargo",
    "Mostre um gráfico de setores por perfil", "Mostre setores por perfil da equipe"])
def test_explicit_team_chart_routes_to_aggregates(question):
    assert is_operational_request(question)


def test_avatar_common_configuration_and_explicit_override(monkeypatch):
    from app.core import config
    with monkeypatch.context() as env:
        for key in ["AVATAR_R2_ENDPOINT", "AVATAR_R2_ACCESS_KEY_ID", "AVATAR_R2_SECRET_ACCESS_KEY", "AVATAR_REDIS_URL"]:
            env.setenv(key, "")
        env.setenv("R2_ENDPOINT", "https://test.r2.cloudflarestorage.com")
        env.setenv("R2_ACCESS_KEY_ID", "test-key")
        env.setenv("R2_SECRET_ACCESS_KEY", "test-value")
        env.setenv("REDIS_URL", "redis://localhost/0")
        importlib.reload(config)
        assert config.AVATAR_R2_ENDPOINT == config.R2_ENDPOINT
        assert config.AVATAR_REDIS_URL == config.REDIS_URL
        assert config.AVATAR_R2_ACCESS_KEY_ID == config.R2_ACCESS_KEY_ID
        env.setenv("AVATAR_R2_ENDPOINT", "https://override.r2.cloudflarestorage.com")
        importlib.reload(config)
        assert config.AVATAR_R2_ENDPOINT != config.R2_ENDPOINT
    importlib.reload(config)


def test_image_publish_embeds_and_checks_revision():
    root = Path(__file__).resolve().parents[1]
    docker = (root / "Dockerfile").read_text()
    workflow = (root / ".github/workflows/publish-image.yml").read_text()
    assert "ARG APP_COMMIT_SHA" in docker and "ENV APP_COMMIT_SHA=" in docker
    assert "org.opencontainers.image.revision=" in docker
    assert '--build-arg APP_COMMIT_SHA="$COMMIT_SHA"' in workflow
    assert "deployed_version(Response())" in workflow


def test_profile_presentation_requires_successful_tool():
    from app.modules.chat.presentation import presentation_evidence
    for name in ("buscar_meus_dados", "consultar_conformidade_usuario"):
        assert presentation_evidence(name, {"status": "ok"}) == {"blocos": ["ficha_usuario"]}
        assert presentation_evidence(name, {"status": "sem_dados"}) is None
