#!/usr/bin/env bash
# Full stack rebuild: destroy → deploy → Phase 1 re-migration. Usage: ./infra/recreate.sh <env>
set -euo pipefail

ENV=${1:?Usage: recreate.sh <env>}

echo "==> [1/2] Destroying existing stack"
./infra/destroy.sh "${ENV}"

echo "==> [2/2] Redeploying from scratch"
./infra/deploy.sh "${ENV}"

echo "==> Recreate complete. Stack is fresh; Phase 1 synthetic data re-migrated."
