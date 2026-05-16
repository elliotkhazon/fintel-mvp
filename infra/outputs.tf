# Networking outputs — consumed by Phases 2-4 modules
output "vpc_id" {
  value = module.networking.vpc_id
}

output "private_subnet_ids" {
  value = module.networking.private_subnet_ids
}

output "sg_k3s_id" {
  value = module.networking.sg_k3s_id
}

output "sg_endpoints_id" {
  value = module.networking.sg_endpoints_id
}

# Storage outputs — consumed by deploy.sh and Phase 4-5 modules
output "transcripts_bucket" {
  value = module.storage.transcripts_bucket
}

output "artifacts_bucket" {
  value = module.storage.artifacts_bucket
}

output "glue_scripts_bucket" {
  value = module.storage.glue_scripts_bucket
}

output "ecr_repo_url" {
  value = module.storage.ecr_repo_url
}

output "la_ecr_repo_url" {
  value = module.storage.la_ecr_repo_url
}

# IAM outputs — consumed by Phases 2, 4, 5
output "ec2_instance_profile_name" {
  value = module.iam.ec2_instance_profile_name
}

output "agentcore_role_arn" {
  value = module.iam.agentcore_role_arn
}

output "glue_role_arn" {
  value = module.iam.glue_role_arn
}

# k3s outputs — consumed by Phase 3 (FastAPI config) and deploy.ps1 SSM commands
output "k3s_instance_id" {
  description = "EC2 instance ID — pass to aws ssm send-command for post-deploy tasks"
  value       = module.k3s.instance_id
}

output "k3s_private_ip" {
  description = "EC2 private IP — used by Phase 3 FastAPI env var SURREALDB_URL"
  value       = module.k3s.private_ip
}

output "surrealdb_host" {
  description = "SurrealDB endpoint (EC2 private IP:8000) consumed by AgentCore and FastAPI"
  value       = module.k3s.surrealdb_host
}

output "kubeconfig_secret_name" {
  description = "Secrets Manager secret name holding the k3s kubeconfig (base64-encoded)"
  value       = module.k3s.kubeconfig_secret_name
}

output "k3s_ssh_key_secret" {
  description = "Secrets Manager secret name holding the k3s SSH private key — fetched by deploy.ps1 for SSH via EIC"
  value       = aws_secretsmanager_secret.k3s_ssh_key.name
}
