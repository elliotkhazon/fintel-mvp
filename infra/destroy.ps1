# Full stack teardown. Usage: .\infra\destroy.ps1 -Env staging
# S3 buckets (module.storage) are excluded - transcript data is preserved across destroys.
# Update the -target list below as new phase modules are added to main.tf.
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("staging", "prod")]
    [string]$Env
)

$ErrorActionPreference = "Stop"
$Vars = "envs/$Env.tfvars"

Write-Host "WARNING: This will destroy ALL infrastructure for env=$Env."
$Confirm = Read-Host "Type the env name to confirm"
if ($Confirm -ne $Env) {
    Write-Host "Aborted."
    exit 1
}

# Targets must match modules declared in main.tf. Expand this list as phases are added.
# module.storage is intentionally excluded to preserve S3 buckets and Secrets Manager secrets.
# module.k3s destroys the EC2 and EBS volume — snapshot the EBS first in prod if needed.
Write-Host "==> Running terraform destroy (module.storage excluded - data preserved)"
terraform -chdir=infra destroy -var-file="$Vars" -auto-approve `
    "-target=module.networking" `
    "-target=module.iam" `
    "-target=module.k3s"
if (-not $?) { exit 1 }

Write-Host "==> Destroy complete."
