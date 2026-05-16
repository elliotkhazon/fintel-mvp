"""Diagnose FastAPI 500 — pulls pod logs and raw curl response via SSM."""

import subprocess
import time
import boto3

ENV = "staging"
REGION = "us-east-1"
INSTANCE_ID = subprocess.check_output(
    "terraform -chdir=infra output -raw k3s_instance_id", shell=True
).decode().strip()


def ssm_run(ssm, cmd, timeout=60):
    resp = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [cmd]},
    )
    cmd_id = resp["Command"]["CommandId"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=INSTANCE_ID)
        if inv["Status"] == "Success":
            return inv["StandardOutputContent"].strip(), inv["StandardErrorContent"].strip()
        if inv["Status"] in ("Failed", "TimedOut", "Cancelled"):
            return inv["StandardOutputContent"].strip(), inv["StandardErrorContent"].strip()
    return "", "timed out"


ssm = boto3.client("ssm", region_name=REGION)

print("=" * 60)
print("1. FastAPI pod logs (last 80 lines)")
print("=" * 60)
out, err = ssm_run(ssm, "kubectl --kubeconfig=/etc/rancher/k3s/k3s.yaml logs deployment/fastapi --tail=80 2>&1", timeout=30)
print(out or err)

print()
print("=" * 60)
print("2. curl without -f (show actual HTTP response)")
print("=" * 60)
cmd = (
    "CLUSTER_IP=$(kubectl --kubeconfig=/etc/rancher/k3s/k3s.yaml get svc fastapi "
    "-o jsonpath='{.spec.clusterIP}') && "
    "curl -s -w '\\nHTTP_STATUS:%{http_code}' "
    "\"http://$CLUSTER_IP:8000/v1/graph/signals/SYN001?quarter=1&year=2020\""
)
out, err = ssm_run(ssm, cmd, timeout=30)
print(out or err)

print()
print("=" * 60)
print("3. SurrealDB pod logs (last 30 lines)")
print("=" * 60)
out, err = ssm_run(ssm, "kubectl --kubeconfig=/etc/rancher/k3s/k3s.yaml logs deployment/surrealdb --tail=30 2>&1", timeout=30)
print(out or err)

print()
print("=" * 60)
print("4. Pod status")
print("=" * 60)
out, err = ssm_run(ssm, "kubectl --kubeconfig=/etc/rancher/k3s/k3s.yaml get pods 2>&1", timeout=30)
print(out or err)
