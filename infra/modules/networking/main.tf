# VPC — private only, no Internet Gateway, no NAT Gateway.
# All AWS service traffic routes through VPC endpoints below.
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

# ── Route table (private subnets) ─────────────────────────────────────────────

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "fintel-private-rt-${var.env}" }
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
