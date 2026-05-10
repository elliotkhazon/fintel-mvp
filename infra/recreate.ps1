# Full stack rebuild: destroy → deploy → Phase 1 re-migration. Usage: .\infra\recreate.ps1 -Env staging
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("staging", "prod")]
    [string]$Env
)

$ErrorActionPreference = "Stop"

Write-Host "==> [1/2] Destroying existing stack"
& "$PSScriptRoot\destroy.ps1" -Env $Env
if (-not $?) { exit 1 }

Write-Host "==> [2/2] Redeploying from scratch"
& "$PSScriptRoot\deploy.ps1" -Env $Env
if (-not $?) { exit 1 }

Write-Host "==> Recreate complete. Stack is fresh; Phase 1 synthetic data re-migrated."
