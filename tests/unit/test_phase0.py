"""Phase 0 unit tests — no AWS credentials required.

Validates that all IaC files exist and have the expected structure.
Runs in CI (ci.yml → unit-tests job) on every PR.
"""
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
INFRA = ROOT / "infra"
MODULES = INFRA / "modules"
WORKFLOWS = ROOT / ".github" / "workflows"


# ── IaC file presence ─────────────────────────────────────────────────────────

def test_infra_root_files():
    for name in ("backend.tf", "main.tf", "variables.tf", "outputs.tf"):
        assert (INFRA / name).is_file(), f"infra/{name} must exist"


def test_module_dirs():
    for module in ("networking", "storage", "iam"):
        module_dir = MODULES / module
        assert module_dir.is_dir(), f"modules/{module}/ must exist"
        for name in ("main.tf", "variables.tf", "outputs.tf"):
            assert (module_dir / name).is_file(), f"modules/{module}/{name} must exist"


def test_envs():
    for env in ("staging", "prod"):
        tfvars = INFRA / "envs" / f"{env}.tfvars"
        assert tfvars.is_file(), f"envs/{env}.tfvars must exist"
        content = tfvars.read_text()
        assert f'env' in content
        assert "aws_region" in content


def test_lifecycle_scripts():
    for script in ("deploy.sh", "destroy.sh", "recreate.sh"):
        assert (INFRA / script).is_file(), f"infra/{script} must exist"


def test_workflow_files():
    for name in ("ci.yml", "deploy-staging.yml", "deploy-prod.yml", "nightly.yml"):
        assert (WORKFLOWS / name).is_file(), f".github/workflows/{name} must exist"


# ── Structural content checks ─────────────────────────────────────────────────

def test_backend_has_s3():
    content = (INFRA / "backend.tf").read_text()
    assert 'backend "s3"' in content


def test_networking_has_no_nat_gateway():
    content = (MODULES / "networking" / "main.tf").read_text()
    assert "aws_nat_gateway" not in content
    assert "aws_internet_gateway" not in content


def test_networking_has_five_endpoints():
    content = (MODULES / "networking" / "main.tf").read_text()
    for endpoint in ("s3", "ecr_dkr", "ecr_api", "secretsmanager", "logs"):
        assert f'aws_vpc_endpoint" "{endpoint}"' in content


def test_storage_has_three_buckets():
    content = (MODULES / "storage" / "main.tf").read_text()
    for key in ("transcripts", "artifacts", "glue_scripts"):
        assert key in content


def test_storage_has_three_secrets():
    content = (MODULES / "storage" / "main.tf").read_text()
    for secret in ("gemini-api-key", "fmp-api-key", "surrealdb-creds"):
        assert secret in content


def test_iam_has_oidc_provider():
    content = (MODULES / "iam" / "main.tf").read_text()
    assert "aws_iam_openid_connect_provider" in content
    assert "token.actions.githubusercontent.com" in content


def test_iam_has_three_service_roles():
    content = (MODULES / "iam" / "main.tf").read_text()
    assert "ec2.amazonaws.com" in content
    assert "bedrock.amazonaws.com" in content
    assert "glue.amazonaws.com" in content


def test_deploy_script_runs_phase1_last():
    content = (INFRA / "deploy.sh").read_text()
    assert "trigger_bulk_extraction.py" in content
    assert "s3 sync" in content
    apply_pos = content.find("terraform")
    sync_pos = content.find("s3 sync")
    assert apply_pos < sync_pos, "terraform apply must precede Phase 1 s3 sync"


def test_ci_workflow_has_oidc_permissions():
    content = (WORKFLOWS / "ci.yml").read_text()
    assert "id-token: write" in content


def test_nightly_has_drift_detection():
    content = (WORKFLOWS / "nightly.yml").read_text()
    assert "-detailed-exitcode" in content
