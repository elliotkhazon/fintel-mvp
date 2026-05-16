resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = "fintel-${var.env}" }
}

resource "aws_subnet" "private" {
  count             = length(var.private_subnet_cidrs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = var.availability_zones[count.index]

  tags = { Name = "fintel-private-${count.index + 1}-${var.env}" }
}

# ── Security Groups ───────────────────────────────────────────────────────────

resource "aws_security_group" "k3s" {
  name        = "fintel-k3s-${var.env}"
  description = "k3s node: SurrealDB (8000) and API server (6443) from VPC"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "SurrealDB from VPC (AgentCore + FastAPI)"
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  ingress {
    description = "k3s API server (internal cluster management)"
    from_port   = 6443
    to_port     = 6443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "fintel-k3s-${var.env}" }
}

resource "aws_security_group_rule" "k3s_ssh_eic" {
  type              = "ingress"
  description       = "SSH via EC2 Instance Connect Endpoint"
  from_port         = 22
  to_port           = 22
  protocol          = "tcp"
  cidr_blocks       = [var.vpc_cidr]
  security_group_id = aws_security_group.k3s.id
}

resource "aws_security_group" "endpoints" {
  name        = "fintel-endpoints-${var.env}"
  description = "VPC Interface endpoints: HTTPS inbound from VPC"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTPS from VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "fintel-endpoints-${var.env}" }
}

# ── Internet Gateway + NAT Gateway ───────────────────────────────────────────

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "fintel-igw-${var.env}" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.public_subnet_cidr
  availability_zone       = var.availability_zones[0]
  map_public_ip_on_launch = false

  tags = { Name = "fintel-public-1-${var.env}" }
}

resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "fintel-nat-eip-${var.env}" }
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public.id
  tags          = { Name = "fintel-nat-${var.env}" }

  depends_on = [aws_internet_gateway.main]
}

# ── Route tables ──────────────────────────────────────────────────────────────

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "fintel-public-rt-${var.env}" }

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "fintel-private-rt-${var.env}" }

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# ── VPC Endpoints ─────────────────────────────────────────────────────────────

# S3 Gateway endpoint — free; no security group needed; routes via route table
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = { Name = "fintel-s3-${var.env}" }
}

# ECR DKR — image layer pulls by k3s/containerd
resource "aws_vpc_endpoint" "ecr_dkr" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.ecr.dkr"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = { Name = "fintel-ecr-dkr-${var.env}" }
}

# ECR API — auth tokens and manifest resolution
resource "aws_vpc_endpoint" "ecr_api" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.ecr.api"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = { Name = "fintel-ecr-api-${var.env}" }
}

# Secrets Manager — credentials fetched by EC2 and AgentCore at runtime
resource "aws_vpc_endpoint" "secretsmanager" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.secretsmanager"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = { Name = "fintel-secretsmanager-${var.env}" }
}

# CloudWatch Logs — pod and agent logs
resource "aws_vpc_endpoint" "logs" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.logs"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = { Name = "fintel-logs-${var.env}" }
}

# SSM — Session Manager and Run Command from private instances.
# Three endpoints are required: ssm (control plane), ssmmessages (session data), ec2messages (Run Command).
# k3s installs nftables rules that block outbound HTTPS to public IPs, so NAT-based SSM does not work.
# VPC endpoints stay within 10.x.x.x (VPC CIDR) and bypass nftables, so they are required.
resource "aws_vpc_endpoint" "ssm" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.ssm"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = { Name = "fintel-ssm-${var.env}" }
}

resource "aws_vpc_endpoint" "ssmmessages" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.ssmmessages"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = { Name = "fintel-ssmmessages-${var.env}" }
}

resource "aws_vpc_endpoint" "ec2messages" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.ec2messages"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true

  tags = { Name = "fintel-ec2messages-${var.env}" }
}

# EC2 Instance Connect Endpoint — SSH to private instances without bastion or public IP.
# Usage: aws ec2-instance-connect ssh --instance-id <id> --region <region> --os-user ubuntu
resource "aws_ec2_instance_connect_endpoint" "main" {
  subnet_id          = aws_subnet.private[0].id
  security_group_ids = [aws_security_group.endpoints.id]
  preserve_client_ip = false

  tags = { Name = "fintel-eic-${var.env}" }
}
