# -------------------------------------------------------------------------
# EC2 Box — one instance per GoE topology box
# -------------------------------------------------------------------------

# Ubuntu 22.04 LTS AMI (matches the Docker base image used in local testing)
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

# ---------- Security Group ----------

locals {
  # Parse "service:port" → list of {name, port} objects
  parsed_services = [
    for svc in var.services : {
      name = split(":", svc)[0]
      port = tonumber(split(":", svc)[1])
    }
  ]

  # SSH-like services get attacker_cidr; web services get 0.0.0.0/0
  ssh_services = ["ssh", "ftp", "smb"]
}

resource "aws_security_group" "this" {
  name_prefix = "goe-${var.run_id}-${var.box_id}-"
  description = substr("GoE ${var.box_id}: ${var.role}", 0, 255)
  vpc_id      = var.vpc_id

  # Ingress rules from topology services
  dynamic "ingress" {
    for_each = local.parsed_services
    content {
      from_port   = ingress.value.port
      to_port     = ingress.value.port
      protocol    = "tcp"
      cidr_blocks = contains(local.ssh_services, ingress.value.name) ? [var.attacker_cidr] : ["0.0.0.0/0"]
      description = "${ingress.value.name} (port ${ingress.value.port})"
    }
  }

  # Allow all traffic within the VPC (inter-box communication)
  ingress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
    description = "Intra-VPC (inter-box)"
  }

  # Allow all outbound (package installs, updates)
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "All outbound"
  }

  tags = {
    Name         = "goe-${var.run_id}-${var.box_id}"
    "goe:box_id" = var.box_id
  }

  lifecycle {
    create_before_destroy = true
  }
}

# ---------- EC2 Instance ----------

resource "aws_instance" "this" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [aws_security_group.this.id]
  iam_instance_profile   = var.ssm_instance_profile_name

  user_data = templatefile("${path.module}/user_data.sh.tpl", {
    hostname = var.hostname
    box_id   = var.box_id
  })

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
    encrypted   = true
  }

  metadata_options {
    http_tokens   = "required" # IMDSv2 only
    http_endpoint = "enabled"
  }

  tags = {
    Name         = "goe-${var.run_id}-${var.box_id}"
    "goe:box_id" = var.box_id
    "goe:role"   = substr(var.role, 0, 255)
  }
}
