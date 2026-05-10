# Fintel MVP — Phase 0 Runbook

## Prerequisites

| Tool | Minimum version | Install |
|---|---|---|
| AWS CLI | v2 | `winget install Amazon.AWSCLI` |
| Terraform | 1.8 | `winget install Hashicorp.Terraform` |
| Python | 3.11 | `winget install Python.Python.3.11` |
| Docker Desktop | latest | https://www.docker.com/products/docker-desktop |

---

## 1. First-time Setup

### 1a. AWS credentials

Configure credentials for the target environment. SSO is preferred:

```bash
aws configure sso
# follow prompts → set profile name e.g. "fintel-staging"
export AWS_PROFILE=fintel-staging
```

Or with static keys (dev only — never commit):
```bash
aws configure
```

Verify access:
```bash
aws sts get-caller-identity
```

### 1b. Remote state bootstrap (one-time per environment)

This is the only AWS CLI exception in the stack — Terraform cannot manage its own
remote state bucket before `terraform init` has run.

Run once per environment, then never again:

**Bash (Linux / macOS / WSL):**
```bash
ENV=staging   # or: prod

aws s3api create-bucket \
  --bucket fintel-tf-state-${ENV} \
  --region us-east-1

aws dynamodb create-table \
  --table-name fintel-tf-locks-${ENV} \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1
```

**PowerShell (Windows):**
```powershell
$Env = "staging"   # or: "prod"

aws s3api create-bucket `
  --bucket "fintel-tf-state-$Env" `
  --region us-east-1

aws dynamodb create-table `
  --table-name "fintel-tf-locks-$Env" `
  --attribute-definitions AttributeName=LockID,AttributeType=S `
  --key-schema AttributeName=LockID,KeyType=HASH `
  --billing-mode PAY_PER_REQUEST `
  --region us-east-1
```

### 1c. Deploy the stack

**Bash (Linux / macOS / WSL):**
```bash
# From repo root
./infra/deploy.sh staging
```

**PowerShell (Windows):**
```powershell
# From repo root
.\infra\deploy.ps1 -Env staging
```

> **One-time PowerShell setup (Windows):** By default PowerShell blocks all local scripts.
> Run this once per machine — it writes to the registry and applies to every future window permanently:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
> ```
> Verify: `Get-ExecutionPolicy -List` → `CurrentUser` row should show `RemoteSigned`.

This runs four steps automatically:
1. `terraform init` (connects to remote state)
2. `terraform plan`
3. `terraform apply` (all Phase 0 resources)
4. Phase 1: `aws s3 sync` + `trigger_bulk_extraction.py` (data migration)

### 1d. Populate secrets

Terraform provisions the secret metadata (name, description, recovery window) but does not
know the actual values. Push values using the `-DeploySecrets` flag on deploy.ps1, which
reads from `.env` by default.

**Recommended — reads values directly from `.env`:**
```powershell
.\infra\deploy.ps1 -Env staging -DeploySecrets
```

`.env` → Secrets Manager mapping:

| `.env` variable | Secrets Manager secret |
|---|---|
| `GOOGLE_API_KEY` | `fintel/gemini-api-key` |
| `FMP_API_KEY` | `fintel/fmp-api-key` |
| `SURREAL_USER` + `SURREAL_PASS` | `fintel/surrealdb-creds` (JSON) |

If a variable is missing from `.env`, that secret is skipped with a `[skip]` message — no error.

**Override individual values** (takes precedence over `.env`):
```powershell
.\infra\deploy.ps1 -Env staging -DeploySecrets `
  -GeminiApiKey "YOUR_GEMINI_KEY" `
  -FmpApiKey "YOUR_FMP_KEY" `
  -SurrealUser "root" `
  -SurrealPass "YOUR_PASS"
```

**Manual AWS CLI fallback (Bash):**
```bash
aws secretsmanager put-secret-value \
  --secret-id fintel/gemini-api-key \
  --secret-string "YOUR_GEMINI_KEY"

aws secretsmanager put-secret-value \
  --secret-id fintel/fmp-api-key \
  --secret-string "YOUR_FMP_KEY"

aws secretsmanager put-secret-value \
  --secret-id fintel/surrealdb-creds \
  --secret-string '{"user":"root","pass":"YOUR_PASS"}'
```

### 1e. Enable GitHub Actions workflows

Workflows are disabled by default. To enable them:

1. Go to **GitHub → repo → Settings → Variables → Repository variables**
2. Create variable: `FINTEL_WORKFLOWS_ENABLED` = `true`

To disable again: set the variable to `false` or delete it.
While disabled, workflow runs are triggered as usual but all jobs are skipped immediately — no AWS calls are made and no costs are incurred.

---

## 2. Running Unit Tests Locally

Unit tests require **no AWS credentials** — they only validate that IaC files and
workflow files exist and have the expected structure.

### Install dependencies

```bash
# From repo root
pip install -r requirements.txt
```

### Run all unit tests

```bash
pytest tests/unit/ -v
```

### Run Phase 0 unit tests only

```bash
pytest tests/unit/test_phase0.py -v
```

### Run the existing functional test suite

```bash
pytest tests/functional/ -v
```

### Run everything together

```bash
pytest tests/unit/ tests/functional/ -v --tb=short
```

---

## 3. Running Integration Tests Locally (requires deployed stack)

Integration tests call live AWS APIs. Set `FINTEL_ENV` before running:

```bash
export FINTEL_ENV=staging
export AWS_PROFILE=fintel-staging

pytest tests/integration/test_phase0.py -v
```

To run only smoke-tagged tests (fastest subset, used in prod deploy gate):

```bash
pytest tests/integration/ -m smoke -v
```

---

## 4. Teardown

**Bash:**
```bash
./infra/destroy.sh staging     # destroy compute/network (preserves S3 data)
./infra/recreate.sh staging    # full rebuild from scratch
```

**PowerShell:**
```powershell
.\infra\destroy.ps1 -Env staging    # destroy compute/network (preserves S3 data)
.\infra\recreate.ps1 -Env staging   # full rebuild from scratch
```

---

## Questions & Answers

**Q: `terraform apply` fails with `AccessDeniedException` on ECR or Secrets Manager — what permissions does `terraformUser` need?**

The IAM user running Terraform locally must have ECR and Secrets Manager permissions. AWS provides a managed policy for ECR; Secrets Manager requires an inline policy. Run once:

```powershell
# ECR — AWS managed policy
aws iam attach-user-policy `
  --user-name terraformUser `
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryFullAccess

# Secrets Manager — no AWS managed policy exists, attach inline
aws iam put-user-policy `
  --user-name terraformUser `
  --policy-name terraform-secretsmanager `
  --policy-document '{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"secretsmanager:*\",\"Resource\":\"*\"}]}'
```

After attaching, re-run the deploy script. Resources that already applied (VPC, subnets, S3 buckets, etc.) will be skipped — Terraform only retries the failed ones.
