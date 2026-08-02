provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      "goe:managed" = "true"
      "goe:run_id"  = var.run_id
    }
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

locals {
  name            = substr(replace(lower(var.run_id), "/[^a-z0-9-]/", "-"), 0, 32)
  private_systems = { for id, system in var.systems : id => system if !system.public }
  needs_nat       = length(local.private_systems) > 0
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "goe-${local.name}" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "goe-${local.name}" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, 1)
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true
  tags                    = { Name = "goe-${local.name}-public" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "goe-${local.name}-public" }
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_subnet" "private" {
  count             = local.needs_nat ? 1 : 0
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, 2)
  availability_zone = data.aws_availability_zones.available.names[0]
  tags              = { Name = "goe-${local.name}-private" }
}

resource "aws_eip" "nat" {
  count  = local.needs_nat ? 1 : 0
  domain = "vpc"
  tags   = { Name = "goe-${local.name}-nat" }
}

resource "aws_nat_gateway" "this" {
  count         = local.needs_nat ? 1 : 0
  allocation_id = aws_eip.nat[0].id
  subnet_id     = aws_subnet.public.id
  depends_on    = [aws_internet_gateway.this]
  tags          = { Name = "goe-${local.name}" }
}

resource "aws_route_table" "private" {
  count  = local.needs_nat ? 1 : 0
  vpc_id = aws_vpc.this.id
  tags   = { Name = "goe-${local.name}-private" }
}

resource "aws_route" "private_nat" {
  count                  = local.needs_nat ? 1 : 0
  route_table_id         = aws_route_table.private[0].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this[0].id
}

resource "aws_route_table_association" "private" {
  count          = local.needs_nat ? 1 : 0
  subnet_id      = aws_subnet.private[0].id
  route_table_id = aws_route_table.private[0].id
}

resource "aws_s3_bucket" "artifacts" {
  bucket_prefix = "goe-${substr(local.name, 0, 20)}-"
  force_destroy = true
  tags          = { Name = "goe-${local.name}-artifacts" }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_object" "script" {
  for_each = var.systems
  bucket   = aws_s3_bucket.artifacts.id
  key      = "scripts/${each.key}.sh"
  source   = "${path.module}/${each.value.script_path}"
  etag     = filemd5("${path.module}/${each.value.script_path}")
  depends_on = [
    aws_s3_bucket_public_access_block.artifacts,
    aws_s3_bucket_server_side_encryption_configuration.artifacts,
  ]
}

resource "aws_iam_role" "instance" {
  name_prefix = "goe-${substr(local.name, 0, 24)}-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
  tags = { Name = "goe-${local.name}-instance" }
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "artifact_read" {
  name = "goe-artifact-read"
  role = aws_iam_role.instance.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject"]
      Resource = "${aws_s3_bucket.artifacts.arn}/scripts/*"
    }]
  })
}

resource "aws_iam_instance_profile" "instance" {
  name_prefix = "goe-${substr(local.name, 0, 24)}-"
  role        = aws_iam_role.instance.name
}

resource "aws_security_group" "system" {
  for_each    = var.systems
  name_prefix = "goe-${substr(local.name, 0, 16)}-${substr(each.key, 0, 16)}-"
  description = "GoE system ${each.key}"
  vpc_id      = aws_vpc.this.id

  dynamic "ingress" {
    for_each = toset(each.value.exposed_ports)
    content {
      description = "GoE exposed TCP ${ingress.value}"
      protocol    = "tcp"
      from_port   = ingress.value
      to_port     = ingress.value
      cidr_blocks = distinct([var.attacker_cidr, var.vpc_cidr])
    }
  }

  dynamic "ingress" {
    for_each = toset(each.value.internal_ports)
    content {
      description = "GoE internal TCP ${ingress.value}"
      protocol    = "tcp"
      from_port   = ingress.value
      to_port     = ingress.value
      cidr_blocks = [var.vpc_cidr]
    }
  }

  egress {
    description = "Package installation and AWS APIs"
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name            = "goe-${local.name}-${each.key}"
    "goe:system_id" = each.key
  }
}

resource "aws_instance" "system" {
  for_each               = var.systems
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = each.value.public ? aws_subnet.public.id : aws_subnet.private[0].id
  vpc_security_group_ids = [aws_security_group.system[each.key].id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name
  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    hostname  = each.value.hostname
    system_id = each.key
  })

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
    encrypted   = true
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  depends_on = [
    aws_iam_role_policy_attachment.ssm,
    aws_iam_role_policy.artifact_read,
    aws_s3_object.script,
  ]

  tags = {
    Name            = "goe-${local.name}-${each.key}"
    "goe:system_id" = each.key
  }
}
