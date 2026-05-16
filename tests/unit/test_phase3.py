"""Phase 3 unit tests — no AWS credentials required.

Validates IaC artefacts (Dockerfile, k8s manifests, deploy.ps1 steps) are
present and structurally correct. Runs in CI (ci.yml → unit-tests job) on
every PR.
"""
from pathlib import Path
import yaml

ROOT = Path(__file__).parent.parent.parent
INFRA = ROOT / "infra"
K8S = INFRA / "k8s"


# ── Dockerfile ────────────────────────────────────────────────────────────────

def test_dockerfile_exists():
    assert (ROOT / "Dockerfile").is_file()


def test_dockerfile_base_image():
    content = (ROOT / "Dockerfile").read_text()
    assert "python:3.11-slim" in content


def test_dockerfile_exposes_8000():
    content = (ROOT / "Dockerfile").read_text()
    assert "EXPOSE 8000" in content


def test_dockerfile_uvicorn_entrypoint():
    content = (ROOT / "Dockerfile").read_text()
    assert "uvicorn" in content
    assert "src.api.main:app" in content


def test_dockerignore_excludes_env():
    di = (ROOT / ".dockerignore").read_text()
    assert ".env" in di
    assert "data/" in di


# ── k8s manifests — fastapi-deployment.yaml ──────────────────────────────────

def test_fastapi_deployment_exists():
    assert (K8S / "fastapi-deployment.yaml").is_file()


def test_fastapi_deployment_replicas():
    content = (K8S / "fastapi-deployment.yaml").read_text()
    doc = next(d for d in yaml.safe_load_all(content) if d and d.get("kind") == "Deployment")
    assert doc["spec"]["replicas"] == 2


def test_fastapi_deployment_image_placeholder():
    content = (K8S / "fastapi-deployment.yaml").read_text()
    assert "FASTAPI_IMAGE" in content, "deploy.ps1 substitutes this placeholder before kubectl apply"


def test_fastapi_deployment_ecr_pull_secret():
    content = (K8S / "fastapi-deployment.yaml").read_text()
    doc = next(d for d in yaml.safe_load_all(content) if d and d.get("kind") == "Deployment")
    pull_secrets = doc["spec"]["template"]["spec"].get("imagePullSecrets", [])
    assert any(s.get("name") == "ecr-creds" for s in pull_secrets)


def test_fastapi_deployment_surreal_url_env():
    content = (K8S / "fastapi-deployment.yaml").read_text()
    assert "surrealdb.default.svc.cluster.local" in content


def test_fastapi_deployment_surrealdb_creds_secret():
    content = (K8S / "fastapi-deployment.yaml").read_text()
    assert "surrealdb-creds" in content


def test_fastapi_deployment_readiness_probe():
    content = (K8S / "fastapi-deployment.yaml").read_text()
    doc = next(d for d in yaml.safe_load_all(content) if d and d.get("kind") == "Deployment")
    container = doc["spec"]["template"]["spec"]["containers"][0]
    probe = container.get("readinessProbe", {})
    assert probe.get("httpGet", {}).get("path") == "/health"


def test_fastapi_deployment_resource_limits():
    content = (K8S / "fastapi-deployment.yaml").read_text()
    doc = next(d for d in yaml.safe_load_all(content) if d and d.get("kind") == "Deployment")
    container = doc["spec"]["template"]["spec"]["containers"][0]
    assert "limits" in container.get("resources", {})
    assert "requests" in container.get("resources", {})


# ── k8s manifests — fastapi-service.yaml ─────────────────────────────────────

def test_fastapi_service_exists():
    assert (K8S / "fastapi-service.yaml").is_file()


def test_fastapi_service_clusterip():
    content = (K8S / "fastapi-service.yaml").read_text()
    doc = yaml.safe_load(content)
    assert doc["spec"]["type"] == "ClusterIP"


def test_fastapi_service_port_8000():
    content = (K8S / "fastapi-service.yaml").read_text()
    doc = yaml.safe_load(content)
    ports = doc["spec"]["ports"]
    assert any(p["port"] == 8000 for p in ports)


# ── deploy.ps1 — Phase 3 steps present ───────────────────────────────────────

def test_deploy_ps1_has_ecr_login():
    content = (INFRA / "deploy.ps1").read_text()
    assert "ecr get-login-password" in content


def test_deploy_ps1_has_docker_build():
    content = (INFRA / "deploy.ps1").read_text()
    assert "docker build" in content
    assert "docker push" in content


def test_deploy_ps1_has_ssm_apply():
    content = (INFRA / "deploy.ps1").read_text()
    assert "ssm send-command" in content
    assert "fastapi-deployment.yaml" in content
    assert "ecr-creds" in content
