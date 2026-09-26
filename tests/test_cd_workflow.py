from pathlib import Path

import yaml


def test_publishing_requires_tests_and_main():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.load(
        (root / ".github/workflows/publish-image.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    assert workflow["on"]["push"]["branches"] == ["main"]
    assert workflow["permissions"] == {"contents": "read"}
    publish = workflow["jobs"]["publish"]
    assert publish["needs"] == "validate"
    assert "github.event_name != 'pull_request'" in publish["if"]
    assert "github.ref == 'refs/heads/main'" in publish["if"]
    assert publish["permissions"] == {"contents": "read", "packages": "write"}
    update = workflow["jobs"]["update-gitops"]
    assert update["needs"] == "publish"
    assert update["permissions"] == {"contents": "read"}
    checkout = next(step for step in update["steps"] if "uses" in step)
    assert checkout["with"]["repository"] == "Astro-Inter/astro-gitops"
    assert checkout["with"]["ref"] == "main"
    assert checkout["with"]["token"] == "${{ secrets.ASTRO_GITOPS_TOKEN }}"
    script = update["steps"][-2]["run"]
    assert "apps/astro-ai-api/overlays/academy/kustomization.yaml" in script
    assert "git switch -c" in script
    assert "HEAD:refs/heads/ci/SCRUM-393-atualizar-ia-" in script
    assert "HEAD:main" not in script
    assert "--force" not in script
    pr = update["steps"][-1]
    assert pr["env"]["GH_TOKEN"] == "${{ secrets.ASTRO_GITOPS_TOKEN }}"
    assert "gh pr create" in pr["run"]
    assert "--base main" in pr["run"]
    assert "gh pr merge" not in pr["run"]
