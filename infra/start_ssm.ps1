param(
    [Parameter(Mandatory = $false)]
    [ValidateSet("staging", "prod")]
    [string]$Env = "staging"
)

$Region = "us-east-1"
$InstanceId = terraform -chdir=infra output -raw k3s_instance_id
if (-not $InstanceId) { Write-Host "[error] Could not get k3s_instance_id from terraform output"; exit 1 }

Write-Host "==> Starting SSM session to $InstanceId ($Env)"
aws ssm start-session --target $InstanceId --region $Region
