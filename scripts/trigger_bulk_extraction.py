#!/usr/bin/env python3
"""Phase 1 (Data Migration): trigger AgentCore extraction-agent for each transcript in S3.

Called by infra/deploy.sh and infra/deploy.ps1 after terraform apply.

Prerequisites:
  - Phase 0: S3 bucket fintel-transcripts-{env} exists (transcripts already synced)
  - Phase 4: AgentCore extraction-agent deployed

If AgentCore is not yet available (Phase 4 not deployed), this script exits cleanly
with a warning so the Phase 0 deploy does not fail.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import boto3
import click
from botocore.exceptions import ClientError, EndpointResolutionError, NoRegionError

REGION = "us-east-1"


def _transcripts_bucket(env: str) -> str:
    return f"fintel-transcripts-{env}"


def _list_transcript_keys(s3, bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(".json"):
                keys.append(obj["Key"])
    return keys


def _find_agent_runtime_id(client, env: str) -> str | None:
    """Return the AgentCore runtime ID for extraction-agent-{env}, or None if not deployed."""
    try:
        resp = client.list_agent_runtimes()
        for runtime in resp.get("agentRuntimeSummaries", []):
            if runtime.get("agentRuntimeName") == f"extraction-agent-{env}":
                return runtime["agentRuntimeId"]
    except Exception:
        pass
    return None


@click.command("trigger-bulk-extraction")
@click.option("--prefix", required=True, help="S3 prefix to scan (e.g. synthetic/)")
@click.option("--env", "deploy_env", required=True, help="Deployment environment (staging | prod)")
def cli(prefix: str, deploy_env: str) -> None:
    """Invoke extraction-agent for every transcript JSON found under PREFIX in S3.

    Skips gracefully if AgentCore (Phase 4) is not yet deployed.
    """
    s3 = boto3.client("s3", region_name=REGION)
    bucket = _transcripts_bucket(deploy_env)

    try:
        agentcore = boto3.client("bedrock-agentcore", region_name=REGION)
        agent_id = _find_agent_runtime_id(agentcore, deploy_env)
    except (EndpointResolutionError, NoRegionError, Exception):
        agent_id = None

    if agent_id is None:
        click.secho(
            f"[Phase 1] extraction-agent-{deploy_env} not found in AgentCore. "
            "Phase 4 (AgentCore) must be deployed before running bulk extraction. "
            "Skipping — re-run deploy.ps1 / deploy.sh after Phase 4 is applied.",
            fg="yellow",
        )
        sys.exit(0)

    click.echo(f"[Phase 1] Scanning s3://{bucket}/{prefix} ...")
    keys = _list_transcript_keys(s3, bucket, prefix)

    if not keys:
        click.secho(f"[Phase 1] No .json files found under s3://{bucket}/{prefix}. "
                    "Run 'aws s3 sync data/transcripts/ s3://{bucket}/synthetic/' first.", fg="yellow")
        sys.exit(0)

    click.echo(f"[Phase 1] Found {len(keys)} transcripts. Invoking extraction-agent ...")

    errors = 0
    for i, key in enumerate(keys, 1):
        try:
            agentcore.invoke_agent_runtime(
                agentRuntimeId=agent_id,
                payload=json.dumps({"s3_bucket": bucket, "s3_key": key, "env": deploy_env}),
            )
            click.echo(f"  [{i}/{len(keys)}] {key} - queued")
        except ClientError as exc:
            click.secho(f"  [{i}/{len(keys)}] {key} - ERROR: {exc}", fg="red")
            errors += 1

    if errors:
        click.secho(f"[Phase 1] Done with {errors} error(s).", fg="red")
        sys.exit(1)

    click.secho(f"[Phase 1] All {len(keys)} transcripts queued for extraction.", fg="green")


if __name__ == "__main__":
    cli()
