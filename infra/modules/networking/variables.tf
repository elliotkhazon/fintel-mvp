variable "env" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "vpc_cidr" {
  type = string
}

variable "private_subnet_cidrs" {
  type = list(string)
}

variable "availability_zones" {
  type = list(string)
}

variable "public_subnet_cidr" {
  type        = string
  default     = "10.0.3.0/24"
  description = "CIDR for the public subnet that hosts the NAT Gateway"
}
