terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # State stored locally per run_id. EC2DeployTool sets the path via -chdir.
  backend "local" {}
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      "goe:managed"  = "true"
      "goe:run_id"   = var.run_id
      "goe:scenario"  = var.scenario_name
      "goe:ttl_hours" = tostring(var.ttl_hours)
    }
  }
}

# --------------------------------------------------------------------------
# VPC (one per scenario)
# --------------------------------------------------------------------------
module "vpc" {
  source = "./modules/vpc"

  run_id   = var.run_id
  vpc_cidr = var.vpc_cidr
}

# --------------------------------------------------------------------------
# EC2 boxes (one per topology box)
# --------------------------------------------------------------------------
module "ec2_box" {
  source   = "./modules/ec2_box"
  for_each = { for box in var.boxes : box.box_id => box }

  run_id        = var.run_id
  box_id        = each.value.box_id
  hostname      = each.value.hostname
  role          = each.value.role
  services      = each.value.services
  instance_type = var.instance_type
  attacker_cidr = var.attacker_cidr

  vpc_id    = module.vpc.vpc_id
  subnet_id = each.value.public ? module.vpc.public_subnet_id : module.vpc.private_subnet_id

  ssm_instance_profile_name = module.vpc.ssm_instance_profile_name
}
