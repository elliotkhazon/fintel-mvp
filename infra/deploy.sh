#!/usr/bin/env bash
# Full stack creation. Usage: ./infra/deploy.sh <env>
# Phase 1 (data migration) runs as the final step, after terraform apply completes all phases.
set -euo pipefail

ENV=${1:?Usage: deploy.sh <env>}
VARS="envs/${ENV}.tfvars"

echo "==> [1/4] Initialising Terraform (remote state)"
terraform -chdir=infra init \
  -backend-config="bucket=fintel-tf-state-${ENV}" \
  -backend-config="key=fintel-mvp/${ENV}.tfstate" \
  -backend-config="dynamodb_table=fintel-tf-locks-${ENV}"

echo "==> [2/4] Planning"
terraform -chdir=infra plan -var-file="${VARS}" -out=tfplan

echo "==> [3/4] Applying"
terraform -chdir=infra apply tfplan

echo "==> [4/4] Phase 1 — syncing synthetic transcripts to S3 (skips existing)"
BUCKET=$(terraform -chdir=infra output -raw transcripts_bucket)
aws s3 sync data/transcripts/ "s3://${BUCKET}/synthetic/" \
  --storage-class STANDARD \
  --size-only
python scripts/trigger_bulk_extraction.py --prefix synthetic/ --env "${ENV}"

echo "==> Deploy complete."
