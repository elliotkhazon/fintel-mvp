terraform {
  required_version = ">= 1.8"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  backend "s3" {
    # Bucket, key, and dynamodb_table are supplied at init time via -backend-config flags.
    # See "Remote State Bootstrap" in docs/cloud_deployment/plan.final.md for the
    # one-time AWS CLI commands that create the state bucket and lock table.
    region  = "us-east-1"
    encrypt = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "fintel-mvp"
      Environment = var.env
      ManagedBy   = "terraform"
    }
  }
}
