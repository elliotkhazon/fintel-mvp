output "transcripts_bucket" {
  value = aws_s3_bucket.buckets["transcripts"].bucket
}

output "artifacts_bucket" {
  value = aws_s3_bucket.buckets["artifacts"].bucket
}

output "glue_scripts_bucket" {
  value = aws_s3_bucket.buckets["glue_scripts"].bucket
}

output "ecr_repo_url" {
  value = aws_ecr_repository.fintel.repository_url
}

output "la_ecr_repo_url" {
  value = aws_ecr_repository.la_engine.repository_url
}

output "gemini_secret_arn" {
  value = aws_secretsmanager_secret.gemini_api_key.arn
}

output "fmp_secret_arn" {
  value = aws_secretsmanager_secret.fmp_api_key.arn
}

output "surrealdb_secret_arn" {
  value = aws_secretsmanager_secret.surrealdb_creds.arn
}
