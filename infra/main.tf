# Phase 0: Foundation — Networking, Storage & Secrets, IAM
# Phases 2-6 modules are added here as each phase is implemented.

module "networking" {
  source = "./modules/networking"

  env                  = var.env
  aws_region           = var.aws_region
  vpc_cidr             = var.vpc_cidr
  private_subnet_cidrs = var.private_subnet_cidrs
  availability_zones   = var.availability_zones
}

module "storage" {
  source = "./modules/storage"

  env = var.env
}

module "iam" {
  source = "./modules/iam"

  env         = var.env
  github_repo = var.github_repo
}
