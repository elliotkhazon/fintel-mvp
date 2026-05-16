"""Phase 3 integration tests — live AWS verification after deploy.ps1 completes.

Requires:
- FINTEL_ENV env var set (default: staging)
- AWS credentials with ssm:SendCommand, ecr:DescribeImages, ec2:DescribeInstances

Run via: pytest tests/integration/test_phase3.py -v
"""
import json
import time
import boto3
import pytest

ENV = pytest.importorskip("os").environ.get("FINTEL_ENV", "staging")
REGION = "us-east-1"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def ecr_client():
    return boto3.client("ecr", region_name=REGION)


@pytest.fixture(scope="session")
def ssm_client():
    return boto3.client("ssm", region_name=REGION)


@pytest.fixture(scope="session")
def ec2_client():
    return boto3.client("ec2", region_name=REGION)


@pytest.fixture(scope="session")
def k3s_instance_id(ec2_client):
    resp = ec2_client.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [f"fintel-k3s-{ENV}"]},
            {"Name": "instance-state-name", "Values": ["running"]},
        ]
    )
    reservations = resp.get("Reservations", [])
    assert reservations, f"No running fintel-k3s-{ENV} EC2 instance found"
    return reservations[0]["Instances"][0]["InstanceId"]


def _ssm_run(ssm_client, instance_id: str, command: str, timeout: int = 60) -> str:
    """Run a shell command via SSM and return stdout. Raises on non-zero exit."""
    resp = ssm_client.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [command]},
    )
    cmd_id = resp["Command"]["CommandId"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        inv = ssm_client.get_command_invocation(
            CommandId=cmd_id, InstanceId=instance_id
        )
        status = inv["Status"]
        if status == "Success":
            return inv["StandardOutputContent"].strip()
        if status in ("Failed", "TimedOut", "Cancelled"):
            raise AssertionError(
                f"SSM command failed ({status}): {inv['StandardErrorContent']}"
            )
    raise TimeoutError(f"SSM command timed out after {timeout}s")


# ── ECR ───────────────────────────────────────────────────────────────────────

@pytest.mark.smoke
def test_fastapi_ecr_image_exists(ecr_client):
    resp = ecr_client.describe_images(
        repositoryName="fintel-mvp",
        imageIds=[{"imageTag": "fastapi"}],
    )
    images = resp.get("imageDetails", [])
    assert len(images) == 1, "fintel-mvp:fastapi image not found in ECR"


# ── k8s deployment ────────────────────────────────────────────────────────────

@pytest.mark.smoke
def test_fastapi_deployment_ready(ssm_client, k3s_instance_id):
    ready = _ssm_run(
        ssm_client,
        k3s_instance_id,
        "kubectl --kubeconfig=/etc/rancher/k3s/k3s.yaml get deployment fastapi "
        "-o jsonpath='{.status.readyReplicas}'",
        timeout=30,
    )
    assert int(ready) >= 1, f"Expected at least 1 ready replica, got: {ready!r}"


def test_fastapi_deployment_desired_replicas(ssm_client, k3s_instance_id):
    desired = _ssm_run(
        ssm_client,
        k3s_instance_id,
        "kubectl --kubeconfig=/etc/rancher/k3s/k3s.yaml get deployment fastapi "
        "-o jsonpath='{.spec.replicas}'",
        timeout=30,
    )
    assert int(desired) == 2


def test_fastapi_service_clusterip_assigned(ssm_client, k3s_instance_id):
    cluster_ip = _ssm_run(
        ssm_client,
        k3s_instance_id,
        "kubectl --kubeconfig=/etc/rancher/k3s/k3s.yaml get svc fastapi "
        "-o jsonpath='{.spec.clusterIP}'",
        timeout=30,
    )
    assert cluster_ip and cluster_ip != "None", "fastapi Service has no ClusterIP"


# ── HTTP health check via ClusterIP ──────────────────────────────────────────

@pytest.mark.smoke
def test_fastapi_health_endpoint(ssm_client, k3s_instance_id):
    output = _ssm_run(
        ssm_client,
        k3s_instance_id,
        "CLUSTER_IP=$(kubectl --kubeconfig=/etc/rancher/k3s/k3s.yaml get svc fastapi "
        "-o jsonpath='{.spec.clusterIP}') && "
        "curl -sf http://$CLUSTER_IP:8000/health",
        timeout=30,
    )
    body = json.loads(output)
    assert body.get("status") == "ok"


def test_fastapi_graph_signals_returns_bundle(ssm_client, k3s_instance_id):
    """Call /v1/graph/signals — expects a SignalBundle even if graph is empty."""
    output = _ssm_run(
        ssm_client,
        k3s_instance_id,
        "CLUSTER_IP=$(kubectl --kubeconfig=/etc/rancher/k3s/k3s.yaml get svc fastapi "
        "-o jsonpath='{.spec.clusterIP}') && "
        "curl -sf http://$CLUSTER_IP:8000/v1/graph/signals/SYN001?quarter=1\\&year=2020",
        timeout=30,
    )
    body = json.loads(output)
    assert "symbol" in body or "detail" in body, (
        "Expected a SignalBundle or 404 detail, got: " + output[:200]
    )
