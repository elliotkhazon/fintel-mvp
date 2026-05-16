variable "env" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "instance_type" {
  type    = string
  default = "t3.medium"
}

variable "ebs_volume_size_gb" {
  type    = number
  default = 20
}

variable "subnet_id" {
  type        = string
  description = "Private subnet ID where the k3s node runs"
}

variable "sg_k3s_id" {
  type        = string
  description = "Security group ID for the k3s node (from networking module)"
}

variable "ec2_instance_profile_name" {
  type        = string
  description = "IAM instance profile name for the k3s node (from IAM module)"
}

variable "ecr_repo_url" {
  type        = string
  description = "ECR repository URL — SurrealDB image is pushed here by deploy.ps1 and pulled by user-data"
}

variable "key_name" {
  type        = string
  description = "EC2 key pair name for SSH access via EIC endpoint"
}
