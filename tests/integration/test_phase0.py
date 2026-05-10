"""Phase 0 integration tests — live AWS verification after terraform apply.

Requires FINTEL_ENV env var and valid AWS credentials (OIDC role assumed by CI).
Run via: pytest tests/integration/test_phase0.py -v
"""
import pytest


# ── S3 Buckets ────────────────────────────────────────────────────────────────

@pytest.mark.smoke
def test_transcripts_bucket_exists(s3_client, env):
    resp = s3_client.head_bucket(Bucket=f"fintel-transcripts-{env}")
    assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200


@pytest.mark.smoke
def test_artifacts_bucket_exists(s3_client, env):
    resp = s3_client.head_bucket(Bucket=f"fintel-artifacts-{env}")
    assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200


def test_glue_scripts_bucket_exists(s3_client, env):
    resp = s3_client.head_bucket(Bucket=f"fintel-glue-scripts-{env}")
    assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200


def test_transcripts_bucket_blocks_public_access(s3_client, env):
    resp = s3_client.get_public_access_block(Bucket=f"fintel-transcripts-{env}")
    cfg = resp["PublicAccessBlockConfiguration"]
    assert cfg["BlockPublicAcls"]
    assert cfg["BlockPublicPolicy"]
    assert cfg["RestrictPublicBuckets"]


def test_transcripts_bucket_encrypted(s3_client, env):
    resp = s3_client.get_bucket_encryption(Bucket=f"fintel-transcripts-{env}")
    rules = resp["ServerSideEncryptionConfiguration"]["Rules"]
    assert any(
        r["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"] == "AES256"
        for r in rules
    )


def test_transcripts_bucket_lifecycle(s3_client, env):
    resp = s3_client.get_bucket_lifecycle_configuration(Bucket=f"fintel-transcripts-{env}")
    rules = resp["Rules"]
    glacier_rule = next(
        (r for r in rules if any(
            t.get("StorageClass") == "GLACIER" for t in r.get("Transitions", [])
        )),
        None,
    )
    assert glacier_rule is not None, "Expected a Glacier transition lifecycle rule"
    assert glacier_rule["Transitions"][0]["Days"] == 90


# ── Secrets Manager ───────────────────────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.parametrize("secret_name", [
    "fintel/gemini-api-key",
    "fintel/fmp-api-key",
    "fintel/surrealdb-creds",
])
def test_secret_exists(sm_client, secret_name):
    resp = sm_client.describe_secret(SecretId=secret_name)
    assert resp["Name"] == secret_name


# ── ECR ───────────────────────────────────────────────────────────────────────

@pytest.mark.smoke
def test_ecr_repository_exists(ecr_client):
    resp = ecr_client.describe_repositories(repositoryNames=["fintel-mvp"])
    repos = resp["repositories"]
    assert len(repos) == 1
    assert repos[0]["imageScanningConfiguration"]["scanOnPush"] is True


# ── VPC Endpoints ─────────────────────────────────────────────────────────────

def test_vpc_endpoints_exist(ec2_client):
    resp = ec2_client.describe_vpc_endpoints(
        Filters=[{"Name": "tag:Project", "Values": ["fintel-mvp"]}]
    )
    endpoints = resp["VpcEndpoints"]
    service_names = [e["ServiceName"] for e in endpoints if e["State"] == "available"]

    expected_suffixes = ["s3", "ecr.dkr", "ecr.api", "secretsmanager", "logs"]
    for suffix in expected_suffixes:
        assert any(sn.endswith(suffix) for sn in service_names), (
            f"No available VPC endpoint found for service suffix '{suffix}'"
        )
