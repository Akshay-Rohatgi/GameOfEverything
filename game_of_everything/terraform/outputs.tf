output "vpc_id" {
  description = "VPC ID for the scenario"
  value       = module.vpc.vpc_id
}

output "boxes" {
  description = "Per-box deployment info"
  value = {
    for box_id, box in module.ec2_box : box_id => {
      instance_id = box.instance_id
      public_ip   = box.public_ip
      private_ip  = box.private_ip
      sg_id       = box.security_group_id
    }
  }
}
