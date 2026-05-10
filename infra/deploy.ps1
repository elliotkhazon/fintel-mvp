# Full stack creation. Usage: .\infra\deploy.ps1 -Env staging [-DeploySecrets]
# Phase 1 (data migration) runs as the final step, after terraform apply completes all phases.
#
# Secret values are read from .env by default when -DeploySecrets is set.
# Override individual values with -GeminiApiKey, -FmpApiKey, -SurrealUser, -SurrealPass.
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("staging", "prod")]
    [string]$Env,

    [switch]$DeploySecrets,

    # Optional overrides — if omitted, values are read from .env
    [string]$GeminiApiKey,
    [string]$FmpApiKey,
    [string]$SurrealUser,
    [string]$SurrealPass
)

$ErrorActionPreference = "Stop"
$RepoRoot  = Split-Path $PSScriptRoot -Parent
$Vars      = "envs/$Env.tfvars"
$DotEnvPath = Join-Path $RepoRoot ".env"

# ── .env parser ───────────────────────────────────────────────────────────────
function Read-DotEnv {
    param([string]$Path)
    $vars = @{}
    if (-not (Test-Path $Path)) { return $vars }
    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#') -and $line -match '^([^=]+)=(.*)$') {
            $vars[$Matches[1].Trim()] = $Matches[2].Trim().Trim('"').Trim("'")
        }
    }
    return $vars
}

# ── Steps 1-3: Terraform ──────────────────────────────────────────────────────

Write-Host "==> [1/4] Initialising Terraform (remote state)"
terraform -chdir=infra init `
    "-backend-config=bucket=fintel-tf-state-$Env" `
    "-backend-config=key=fintel-mvp/$Env.tfstate" `
    "-backend-config=dynamodb_table=fintel-tf-locks-$Env"
if (-not $?) { exit 1 }

Write-Host "==> [2/4] Planning"
terraform -chdir=infra plan -var-file="$Vars" -out=tfplan
if (-not $?) { exit 1 }

Write-Host "==> [3/4] Applying"
terraform -chdir=infra apply tfplan
if (-not $?) { exit 1 }

# ── Optional: push secrets to Secrets Manager ─────────────────────────────────

if ($DeploySecrets) {
    Write-Host "==> [3b/4] Deploying secrets to AWS Secrets Manager (source: .env)"
    $dotenv = Read-DotEnv -Path $DotEnvPath

    # fintel/gemini-api-key  <-- GOOGLE_API_KEY in .env
    $gemKey = if ($GeminiApiKey) { $GeminiApiKey } else { $dotenv["GOOGLE_API_KEY"] }
    if ($gemKey) {
        aws secretsmanager put-secret-value --secret-id fintel/gemini-api-key --secret-string $gemKey
        if (-not $?) { exit 1 }
        Write-Host "  [ok] fintel/gemini-api-key"
    } else {
        Write-Host "  [skip] fintel/gemini-api-key - GOOGLE_API_KEY not found in .env"
    }

    # fintel/fmp-api-key  <-- FMP_API_KEY in .env
    $fmpKey = if ($FmpApiKey) { $FmpApiKey } else { $dotenv["FMP_API_KEY"] }
    if ($fmpKey) {
        aws secretsmanager put-secret-value --secret-id fintel/fmp-api-key --secret-string $fmpKey
        if (-not $?) { exit 1 }
        Write-Host "  [ok] fintel/fmp-api-key"
    } else {
        Write-Host "  [skip] fintel/fmp-api-key - FMP_API_KEY not found in .env"
    }

    # fintel/surrealdb-creds  <-- SURREAL_USER + SURREAL_PASS in .env
    $sUser = if ($SurrealUser) { $SurrealUser } else { $dotenv["SURREAL_USER"] }
    $sPass = if ($SurrealPass) { $SurrealPass } else { $dotenv["SURREAL_PASS"] }
    if ($sUser -and $sPass) {
        $creds = "{`"user`":`"$sUser`",`"pass`":`"$sPass`"}"
        aws secretsmanager put-secret-value --secret-id fintel/surrealdb-creds --secret-string $creds
        if (-not $?) { exit 1 }
        Write-Host "  [ok] fintel/surrealdb-creds"
    } else {
        Write-Host "  [skip] fintel/surrealdb-creds - SURREAL_USER or SURREAL_PASS not found in .env"
    }
}

# ── Step 4: Phase 1 data migration ───────────────────────────────────────────

Write-Host "==> [4/4] Phase 1 - syncing synthetic transcripts to S3 (skips existing)"
$Bucket = terraform -chdir=infra output -raw transcripts_bucket
if (-not $?) { exit 1 }

aws s3 sync data/transcripts/ "s3://$Bucket/synthetic/" --storage-class STANDARD --size-only
if (-not $?) { exit 1 }

python scripts/trigger_bulk_extraction.py --prefix synthetic/ --env $Env
if (-not $?) { exit 1 }

Write-Host "==> Deploy complete."
