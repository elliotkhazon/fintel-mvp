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
