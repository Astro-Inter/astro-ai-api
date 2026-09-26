from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "deploy/k8s/overlays/academy"


def test_academy_uses_published_ghcr_image_without_ingress():
    overlay = yaml.safe_load((OVERLAY / "kustomization.yaml").read_text())
    assert overlay["resources"] == ["../../base"]
    assert overlay["images"][0]["newName"] == "ghcr.io/astro-inter/astro-ai-api"
    assert re.fullmatch(r"sha-[0-9a-f]{40}", overlay["images"][0]["newTag"])


def test_academy_has_one_replica_and_private_registry_secret():
    patch = yaml.safe_load((OVERLAY / "deployment-patch.yaml").read_text())
    assert patch["spec"]["replicas"] == 1
    assert patch["spec"]["strategy"]["rollingUpdate"] == {
        "maxSurge": 0, "maxUnavailable": 1,
    }
    assert patch["spec"]["template"]["spec"]["imagePullSecrets"] == [
        {"name": "ghcr-read"},
    ]


def test_argocd_watches_the_gitops_branch_in_the_local_cluster():
    project, application = yaml.safe_load_all(
        (ROOT / "deploy/argocd/academy.yaml").read_text(),
    )
    assert application["spec"]["project"] == project["metadata"]["name"]
    assert application["spec"]["source"]["targetRevision"] == (
        "deploy/SCRUM-393-gitops-aws-academy"
    )
    assert application["spec"]["source"]["path"] == "deploy/k8s/overlays/academy"
    assert application["spec"]["destination"] == {
        "server": "https://kubernetes.default.svc", "namespace": "astro-ai",
    }


def test_publishing_requires_tests_and_main_and_uses_scoped_permissions():
    # BaseLoader preserves the YAML key "on" instead of coercing it to bool.
    workflow = yaml.load(
        (ROOT / ".github/workflows/publish-image.yml").read_text(),
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
    assert update["permissions"] == {"contents": "write"}
    assert update["env"]["GITOPS_BRANCH"] == "deploy/SCRUM-393-gitops-aws-academy"
