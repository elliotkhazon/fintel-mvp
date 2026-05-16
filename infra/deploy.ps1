# Full stack creation. Usage: .\infra\deploy.ps1 -Env staging [-DeploySecrets]
# Phase 1 (data migration) and Phase 3 (FastAPI on k3s) run after terraform apply.
# Requires Docker Desktop running locally for the Phase 3 image build.
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
$Vars      = Join-Path $PSScriptRoot "envs\$Env.tfvars"
$DotEnvPath = Join-Path $RepoRoot ".env"
$Region    = "us-east-1"

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

Write-Host "==> [1/6] Initialising Terraform (remote state)"
terraform -chdir=infra init -reconfigure `
    "-backend-config=bucket=fintel-tf-state-$Env" `
    "-backend-config=key=fintel-mvp/$Env.tfstate"
if (-not $?) { exit 1 }

Write-Host "==> [2/6] Planning"
terraform -chdir=infra plan -var-file="$Vars" -out=tfplan
if (-not $?) { exit 1 }

Write-Host "==> [3/6] Applying"
terraform -chdir=infra apply tfplan
if (-not $?) { exit 1 }

# ── Step 3b: Push SurrealDB image to ECR ─────────────────────────────────────
# The private VPC has no internet — Docker Hub is unreachable from EC2.
# Push SurrealDB to ECR immediately after terraform apply so the image is ready
# before user-data step 4 tries to pull it. The ECR VPC endpoint handles the rest.

Write-Host "==> [3b/6] Pushing SurrealDB image to ECR"
$EcrUrl = terraform -chdir=infra output -raw ecr_repo_url
if (-not $?) { exit 1 }
$SurrealImage = "${EcrUrl}:surrealdb"

$surrealInEcr = $false
try {
    $null = aws ecr describe-images `
        --repository-name fintel-mvp `
        --image-ids imageTag=surrealdb `
        --region $Region --output json 2>&1
    if ($LASTEXITCODE -eq 0) { $surrealInEcr = $true }
} catch {}
if ($surrealInEcr) {
    Write-Host "  [skip] surrealdb image already in ECR"
} else {
    $EcrToken = aws ecr get-login-password --region $Region
    $EcrRegistry = $EcrUrl.Split('/')[0]
    $tokenFile = "$env:TEMP\ecr-token.txt"
    [System.IO.File]::WriteAllText($tokenFile, $EcrToken, (New-Object System.Text.ASCIIEncoding))
    $ErrorActionPreference = "Continue"
    cmd /c "type `"$tokenFile`" | docker login --username AWS --password-stdin $EcrRegistry"
    Remove-Item $tokenFile -Force -ErrorAction SilentlyContinue
    if ($LASTEXITCODE -ne 0) { $ErrorActionPreference = "Stop"; Write-Host "  [error] docker login failed"; exit 1 }
    docker pull surrealdb/surrealdb:latest
    if ($LASTEXITCODE -ne 0) { $ErrorActionPreference = "Stop"; exit 1 }
    docker tag surrealdb/surrealdb:latest $SurrealImage
    docker push $SurrealImage
    $ErrorActionPreference = "Stop"
    if ($LASTEXITCODE -ne 0) { Write-Host "  [error] docker push surrealdb failed"; exit 1 }
    Write-Host "  [ok] surrealdb image pushed to $SurrealImage"
}

# ── Optional: push secrets to Secrets Manager ─────────────────────────────────

if ($DeploySecrets) {
    Write-Host "==> [3b/6] Deploying secrets to AWS Secrets Manager (source: .env)"
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
        $creds = '{"user":"' + $sUser + '","pass":"' + $sPass + '"}'
        $credsFile = "$env:TEMP\sdb-creds.json"
        [System.IO.File]::WriteAllText($credsFile, $creds, (New-Object System.Text.UTF8Encoding $false))
        aws secretsmanager put-secret-value --secret-id fintel/surrealdb-creds --secret-string "file://$credsFile"
        Remove-Item $credsFile -Force -ErrorAction SilentlyContinue
        if (-not $?) { exit 1 }
        Write-Host "  [ok] fintel/surrealdb-creds"
    } else {
        Write-Host "  [skip] fintel/surrealdb-creds - SURREAL_USER or SURREAL_PASS not found in .env"
    }
}

# ── Step 4: Phase 1 data migration ───────────────────────────────────────────

Write-Host "==> [4/6] Phase 1 - syncing synthetic transcripts to S3 (skips existing)"
$Bucket = terraform -chdir=infra output -raw transcripts_bucket
if (-not $?) { exit 1 }

aws s3 sync data/transcripts/ "s3://$Bucket/synthetic/" --storage-class STANDARD --size-only
if (-not $?) { exit 1 }

& "$RepoRoot\etl-env\Scripts\python.exe" scripts/trigger_bulk_extraction.py --prefix synthetic/ --env $Env
if (-not $?) { exit 1 }

# ── Steps 5-6: Phase 3 — FastAPI on k3s ──────────────────────────────────────

$EcrUrl          = terraform -chdir=infra output -raw ecr_repo_url
if (-not $?) { exit 1 }
$ArtifactsBucket = terraform -chdir=infra output -raw artifacts_bucket
if (-not $?) { exit 1 }
$InstanceId      = terraform -chdir=infra output -raw k3s_instance_id
if (-not $?) { exit 1 }
$FastApiImage    = "${EcrUrl}:fastapi"

Write-Host "==> [5/6] Phase 3 - building and pushing FastAPI image to ECR"
$EcrRegistry = $EcrUrl.Split('/')[0]

$EcrToken = aws ecr get-login-password --region $Region
if ($LASTEXITCODE -ne 0) { Write-Host "  [error] ecr get-login-password failed"; exit 1 }
$tokenFile = "$env:TEMP\ecr-token.txt"
[System.IO.File]::WriteAllText($tokenFile, $EcrToken, (New-Object System.Text.ASCIIEncoding))
$ErrorActionPreference = "Continue"
cmd /c "type `"$tokenFile`" | docker login --username AWS --password-stdin $EcrRegistry"
$loginExit = $LASTEXITCODE
Remove-Item $tokenFile -Force -ErrorAction SilentlyContinue
if ($loginExit -ne 0) { $ErrorActionPreference = "Stop"; Write-Host "  [error] docker login failed"; exit 1 }
docker build -t $FastApiImage "$RepoRoot"
$buildExit = $LASTEXITCODE
if ($buildExit -ne 0) { $ErrorActionPreference = "Stop"; Write-Host "  [error] docker build failed"; exit 1 }
docker push $FastApiImage
$pushExit = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($pushExit -ne 0) { Write-Host "  [error] docker push failed"; exit 1 }

Write-Host "==> [6/6] Phase 3 - deploying FastAPI manifests to k3s via SSH"

# Substitute ECR image URL into deployment manifest and upload both to S3.
# Set-Content -Encoding utf8 adds a BOM in PS 5.1, which AWS CLI rejects —
# use [System.IO.File]::WriteAllText with explicit no-BOM encoder throughout.
$utf8NoBom = New-Object System.Text.UTF8Encoding $false

$ManifestContent = (Get-Content "$RepoRoot\infra\k8s\fastapi-deployment.yaml" -Raw) `
    -replace 'FASTAPI_IMAGE', $FastApiImage
$TmpManifest = "$env:TEMP\fastapi-deployment.yaml"
[System.IO.File]::WriteAllText($TmpManifest, $ManifestContent, $utf8NoBom)
aws s3 cp $TmpManifest "s3://$ArtifactsBucket/k8s/fastapi-deployment.yaml" --region $Region
if (-not $?) { exit 1 }
aws s3 cp "$RepoRoot\infra\k8s\fastapi-service.yaml" "s3://$ArtifactsBucket/k8s/fastapi-service.yaml" --region $Region
if (-not $?) { exit 1 }

# Write a self-contained bash deploy script to S3.
# PS expands $Region / $EcrUrl / $ArtifactsBucket; backtick-dollar keeps bash
# variables ($REGION, $TOKEN, etc.) and $() substitutions unexpanded by PS.
$deployScript = @"
#!/bin/bash
set -euo pipefail
export PATH=`$PATH:/usr/local/bin:/usr/bin:/snap/bin
REGION='$Region'
ECR_URL='$EcrUrl'
BUCKET='$ArtifactsBucket'
KUBECTL='/usr/local/bin/kubectl --kubeconfig=/etc/rancher/k3s/k3s.yaml'

echo "Waiting for k3s to finish bootstrapping..."
until [ -f /usr/local/bin/kubectl ]; do sleep 10; done
until /usr/local/bin/kubectl --kubeconfig=/etc/rancher/k3s/k3s.yaml get nodes 2>/dev/null | grep -q " Ready"; do sleep 10; done
echo "k3s ready"

echo "Waiting for user_data to deploy SurrealDB (surrealdb-creds secret)..."
until `$KUBECTL get secret surrealdb-creds 2>/dev/null; do
  echo "  not ready yet, retrying in 15s..."
  sleep 15
done
echo "SurrealDB ready"

TOKEN=`$(aws ecr get-login-password --region `$REGION)
`$KUBECTL create secret docker-registry ecr-creds --docker-server=`$ECR_URL --docker-username=AWS --docker-password=`$TOKEN --dry-run=client -o yaml | `$KUBECTL apply -f -
aws s3 cp s3://`$BUCKET/k8s/fastapi-deployment.yaml /tmp/fastapi-deployment.yaml --region `$REGION
aws s3 cp s3://`$BUCKET/k8s/fastapi-service.yaml /tmp/fastapi-service.yaml --region `$REGION
`$KUBECTL apply -f /tmp/fastapi-deployment.yaml
`$KUBECTL apply -f /tmp/fastapi-service.yaml
`$KUBECTL rollout restart deployment/fastapi
`$KUBECTL rollout status deployment/fastapi --timeout=300s
"@
$TmpScript = "$env:TEMP\deploy_fastapi.sh"
[System.IO.File]::WriteAllText($TmpScript, $deployScript.Replace("`r`n", "`n"), $utf8NoBom)
aws s3 cp $TmpScript "s3://$ArtifactsBucket/scripts/deploy_fastapi.sh" --region $Region
if (-not $?) { exit 1 }

# Wait for instance running state.
Write-Host "  Waiting for instance running state..."
aws ec2 wait instance-running --instance-ids $InstanceId --region $Region
if (-not $?) { exit 1 }

# Wait for SSM agent to register (reaches SSM via VPC Interface endpoints).
Write-Host "  Waiting for SSM agent to register (up to 10 min)..."
$ssmReady = $false
$ErrorActionPreference = "Continue"
for ($i = 0; $i -lt 60; $i++) {
    $rawSsm = aws ssm describe-instance-information `
        --filters "Key=InstanceIds,Values=$InstanceId" `
        --region $Region --output json 2>$null
    if ($LASTEXITCODE -eq 0 -and $rawSsm) {
        $ssmInfo = $rawSsm | ConvertFrom-Json
        if ($ssmInfo.InstanceInformationList.Count -gt 0) { $ssmReady = $true; break }
    }
    Write-Host "    [$($i * 10)s] SSM not ready..."
    Start-Sleep -Seconds 10
}
$ErrorActionPreference = "Stop"
if (-not $ssmReady) { Write-Host "  [error] SSM agent did not register within 10 min"; exit 1 }
Write-Host "  [ok] SSM agent ready"

# Run deploy_fastapi.sh on instance via SSM Run Command.
Write-Host "  Running deploy_fastapi.sh via SSM..."
$ssmCmd = "until [ -f /usr/local/bin/aws ]; do echo 'waiting for AWS CLI (user_data step 0a)...'; sleep 10; done && /usr/local/bin/aws s3 cp s3://$ArtifactsBucket/scripts/deploy_fastapi.sh /tmp/deploy_fastapi.sh --region $Region && bash /tmp/deploy_fastapi.sh"
$ssmInputJson = (@{
    DocumentName   = "AWS-RunShellScript"
    InstanceIds    = @($InstanceId)
    Parameters     = @{ commands = @($ssmCmd) }
    TimeoutSeconds = 900
    Comment        = "fintel deploy_fastapi"
} | ConvertTo-Json -Depth 5 -Compress)
$ssmInputFile = "$env:TEMP\ssm-send.json"
[System.IO.File]::WriteAllText($ssmInputFile, $ssmInputJson, (New-Object System.Text.UTF8Encoding $false))

$sendResult = aws ssm send-command --cli-input-json "file://$ssmInputFile" --region $Region --output json | ConvertFrom-Json
if (-not $sendResult) { Write-Host "  [error] SSM send-command failed"; exit 1 }
$CommandId = $sendResult.Command.CommandId
Write-Host "  SSM command: $CommandId"

# Poll until complete — deploy_fastapi.sh waits for SurrealDB, can take ~15 min.
$startTime = Get-Date
$ErrorActionPreference = "Continue"
do {
    Start-Sleep -Seconds 15
    $inv = aws ssm get-command-invocation `
        --command-id $CommandId --instance-id $InstanceId `
        --region $Region --output json 2>$null | ConvertFrom-Json
    $elapsed = [math]::Round(((Get-Date) - $startTime).TotalSeconds)
    Write-Host "  [${elapsed}s] $($inv.Status)"
} while ($inv.Status -in @('Pending', 'InProgress', 'Delayed'))
$ErrorActionPreference = "Stop"

if ($inv.Status -ne 'Success') {
    Write-Host "  [error] SSM command failed: $($inv.StatusDetails)"
    Write-Host $inv.StandardErrorContent
    exit 1
}
Write-Host $inv.StandardOutputContent

Write-Host "==> Deploy complete."
