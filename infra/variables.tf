variable "env" {
  type        = string
  description = "Deployment environment (staging | prod)"
  validation {
    condition     = contains(["staging", "prod"], var.env)
    error_message = "env must be staging or prod"
  }
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "private_subnet_cidrs" {
  type    = list(string)
  default = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "availability_zones" {
  type    = list(string)
  default = ["us-east-1a", "us-east-1b"]
}

variable "public_subnet_cidr" {
  type        = string
  default     = "10.0.3.0/24"
  description = "CIDR for the public subnet that hosts the NAT Gateway"
}

variable "ec2_instance_type" {
  type    = string
  default = "t3.medium"
}

variable "ebs_volume_size_gb" {
  type    = number
  default = 20
}

variable "agentcore_concurrency_limit" {
  type    = number
  default = 5
}

variable "github_repo" {
  type        = string
  default     = "elliotkhazon/fintel-mvp"
  description = "GitHub repo in owner/repo format for OIDC trust policy"
}
