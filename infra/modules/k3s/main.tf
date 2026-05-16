data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

data "aws_subnet" "selected" {
  id = var.subnet_id
}

# ── Kubeconfig secret ─────────────────────────────────────────────────────────
# Shell created here so user-data can put-secret-value immediately after k3s init.
# Value is empty until first boot completes.

resource "aws_secretsmanager_secret" "kubeconfig" {
  name                    = "fintel/kubeconfig-${var.env}"
  description             = "k3s kubeconfig for ${var.env} — base64-encoded; written by EC2 user-data on first boot"
  recovery_window_in_days = var.env == "staging" ? 0 : 30
  tags                    = { Name = "fintel-kubeconfig-${var.env}" }
}

# ── EBS data volume ───────────────────────────────────────────────────────────

resource "aws_ebs_volume" "surrealdb" {
  availability_zone = data.aws_subnet.selected.availability_zone
  size              = var.ebs_volume_size_gb
  type              = "gp3"
  encrypted         = true
  tags              = { Name = "fintel-surrealdb-data-${var.env}" }
}

# ── k3s EC2 instance ──────────────────────────────────────────────────────────

resource "aws_instance" "k3s" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = [var.sg_k3s_id]
  iam_instance_profile        = var.ec2_instance_profile_name
  associate_public_ip_address = false
  key_name                    = var.key_name

  user_data = base64encode(templatefile("${path.module}/user_data.sh.tpl", {
    env          = var.env
    aws_region   = var.aws_region
    ecr_repo_url = var.ecr_repo_url
  }))

  root_block_device {
    volume_type = "gp3"
    volume_size = 30
    encrypted   = true
  }

  tags = { Name = "fintel-k3s-${var.env}" }

  # Prevent replacement when AMI is updated — requires manual instance refresh.
  lifecycle {
    ignore_changes = [ami]
  }
}

resource "aws_volume_attachment" "surrealdb" {
  device_name  = "/dev/xvdf"
  volume_id    = aws_ebs_volume.surrealdb.id
  instance_id  = aws_instance.k3s.id
  force_detach = false
}
