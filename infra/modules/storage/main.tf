locals {
  buckets = {
    transcripts  = "fintel-transcripts-${var.env}"
    artifacts    = "fintel-artifacts-${var.env}"
    glue_scripts = "fintel-glue-scripts-${var.env}"
  }
}

# ── S3 Buckets ────────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "buckets" {
  for_each = local.buckets
  bucket   = each.value

  # staging: allow terraform destroy to empty the bucket automatically
  force_destroy = var.env == "staging"

  tags = { Name = each.value }
}

resource "aws_s3_bucket_versioning" "buckets" {
  for_each = aws_s3_bucket.buckets
  bucket   = each.value.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "buckets" {
  for_each = aws_s3_bucket.buckets
  bucket   = each.value.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "buckets" {
  for_each                = aws_s3_bucket.buckets
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Archive transcripts to Glacier after 90 days — no recurring cost change (already ~$0.001/month)
resource "aws_s3_bucket_lifecycle_configuration" "transcripts" {
  bucket = aws_s3_bucket.buckets["transcripts"].id

  rule {
    id     = "archive-to-glacier"
    status = "Enabled"

    filter { prefix = "" }

    transition {
      days          = 90
      storage_class = "GLACIER"
    }
  }
}

# ── ECR Repositories ──────────────────────────────────────────────────────────

resource "aws_ecr_repository" "fintel" {
  name                 = "fintel-mvp"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Name = "fintel-mvp" }
}

resource "aws_ecr_lifecycle_policy" "fintel" {
  repository = aws_ecr_repository.fintel.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 10 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

# LA Engine GPU container image — separate from the main app image so lifecycle
# policies and IAM pull permissions can be scoped independently
resource "aws_ecr_repository" "la_engine" {
  name                 = "fintel-la-engine"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Name = "fintel-la-engine" }
}

resource "aws_ecr_lifecycle_policy" "la_engine" {
  repository = aws_ecr_repository.la_engine.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 5 LA Engine images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = { type = "expire" }
    }]
  })
}

# ── Secrets Manager ───────────────────────────────────────────────────────────
# Secret values are populated manually or via CI after terraform apply.
# Terraform only provisions the secret metadata (name, description, recovery window).

resource "aws_secretsmanager_secret" "gemini_api_key" {
  name                    = "fintel/gemini-api-key"
  description             = "Gemini API key for AgentCore agents (transcript, extraction, prediction)"
  recovery_window_in_days = var.env == "staging" ? 0 : 30
}

resource "aws_secretsmanager_secret" "fmp_api_key" {
  name                    = "fintel/fmp-api-key"
  description             = "Financial Modeling Prep API key for fundamentals and transcript fetch"
  recovery_window_in_days = var.env == "staging" ? 0 : 30
}

resource "aws_secretsmanager_secret" "surrealdb_creds" {
  name                    = "fintel/surrealdb-creds"
  description             = "SurrealDB root credentials — JSON: {\"user\": \"...\", \"pass\": \"...\"}"
  recovery_window_in_days = var.env == "staging" ? 0 : 30
}
