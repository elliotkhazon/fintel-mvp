#!/usr/bin/env bash
# Full stack teardown. Usage: ./infra/destroy.sh <env>
# module.storage is excluded — transcript data is preserved across destroys.
# Update the -target list below as new phase modules are added to main.tf.
set -euo pipefail

ENV=${1:?Usage: destroy.sh <env>}
VARS="envs/${ENV}.tfvars"

echo "WARNING: This will destroy ALL infrastructure for env=${ENV}."
read -rp "Type the env name to confirm: " CONFIRM
[[ "${CONFIRM}" != "${ENV}" ]] && echo "Aborted." && exit 1

# Targets must match modules declared in main.tf. Expand this list as phases are added.
# module.storage is intentionally excluded to preserve S3 buckets and Secrets Manager secrets.
echo "==> Running terraform destroy (module.storage excluded - data preserved)"
terraform -chdir=infra destroy -var-file="${VARS}" -auto-approve \
  -target=module.networking \
  -target=module.iam

echo "==> Destroy complete."
