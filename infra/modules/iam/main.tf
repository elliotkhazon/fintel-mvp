data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name
}

# ── GitHub Actions OIDC ───────────────────────────────────────────────────────
# Allows GitHub Actions to assume AWS roles without long-lived access keys.

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

resource "aws_iam_role" "ci" {
  for_each = toset(["staging", "prod"])
  name     = "fintel-ci-${each.key}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repo}:environment:${each.key}"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "ci_terraform" {
  for_each = aws_iam_role.ci
  name     = "terraform-ops"
  role     = each.value.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "TerraformState"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
        Resource = [
          "arn:aws:s3:::fintel-tf-state-${each.key}",
          "arn:aws:s3:::fintel-tf-state-${each.key}/*"
        ]
      },
      {
        Sid    = "TerraformLock"
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
        Resource = "arn:aws:dynamodb:${local.region}:${local.account_id}:table/fintel-tf-locks-${each.key}"
      },
      {
        Sid    = "ProvisionResources"
        Effect = "Allow"
        Action = [
          "ec2:*",
          "s3:*",
          "ecr:*",
          "secretsmanager:*",
          "iam:*",
          "bedrock:*",
          "glue:*",
          "sagemaker:*",
          "logs:*",
          "cloudwatch:*"
        ]
        Resource = "*"
      }
    ]
  })
}

# ── EC2 Role (k3s node — Phase 2) ────────────────────────────────────────────

resource "aws_iam_role" "ec2" {
  name = "fintel-ec2-role-${var.env}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_instance_profile" "ec2" {
  name = "fintel-ec2-profile-${var.env}"
  role = aws_iam_role.ec2.name
}

resource "aws_iam_role_policy" "ec2_policy" {
  name = "fintel-ec2-policy"
  role = aws_iam_role.ec2.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ECRPull"
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage"
        ]
        Resource = "*"
      },
      {
        Sid    = "SecretsRead"
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
        Resource = "arn:aws:secretsmanager:${local.region}:${local.account_id}:secret:fintel/*"
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams"
        ]
        Resource = "arn:aws:logs:${local.region}:${local.account_id}:log-group:/fintel/*"
      },
      {
        Sid    = "S3TranscriptsRead"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          "arn:aws:s3:::fintel-transcripts-${var.env}",
          "arn:aws:s3:::fintel-transcripts-${var.env}/*"
        ]
      }
    ]
  })
}

# ── AgentCore Role (Phase 4) ──────────────────────────────────────────────────

resource "aws_iam_role" "agentcore" {
  name = "fintel-agentcore-role-${var.env}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "agentcore_policy" {
  name = "fintel-agentcore-policy"
  role = aws_iam_role.agentcore.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SecretsRead"
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
        Resource = "arn:aws:secretsmanager:${local.region}:${local.account_id}:secret:fintel/*"
      },
      {
        Sid    = "S3ReadWrite"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
        Resource = [
          "arn:aws:s3:::fintel-transcripts-${var.env}",
          "arn:aws:s3:::fintel-transcripts-${var.env}/*",
          "arn:aws:s3:::fintel-artifacts-${var.env}",
          "arn:aws:s3:::fintel-artifacts-${var.env}/*"
        ]
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${local.region}:${local.account_id}:log-group:/fintel/*"
      }
    ]
  })
}

# ── Glue Role (Phase 5) ───────────────────────────────────────────────────────

resource "aws_iam_role" "glue" {
  name = "fintel-glue-role-${var.env}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "glue.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "glue_service" {
  role       = aws_iam_role.glue.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

resource "aws_iam_role_policy" "glue_policy" {
  name = "fintel-glue-policy"
  role = aws_iam_role.glue.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SecretsRead"
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = "arn:aws:secretsmanager:${local.region}:${local.account_id}:secret:fintel/*"
      },
      {
        Sid    = "S3Access"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
        Resource = [
          "arn:aws:s3:::fintel-transcripts-${var.env}",
          "arn:aws:s3:::fintel-transcripts-${var.env}/*",
          "arn:aws:s3:::fintel-artifacts-${var.env}",
          "arn:aws:s3:::fintel-artifacts-${var.env}/*",
          "arn:aws:s3:::fintel-glue-scripts-${var.env}",
          "arn:aws:s3:::fintel-glue-scripts-${var.env}/*"
        ]
      }
    ]
  })
}
