output "vpc_id" {
  value = aws_vpc.this.id
}

output "artifact_bucket" {
  value = aws_s3_bucket.artifacts.id
}

output "systems" {
  value = {
    for id, instance in aws_instance.system : id => {
      instance_id       = instance.id
      public_ip         = instance.public_ip
      private_ip        = instance.private_ip
      security_group_id = aws_security_group.system[id].id
      artifact_key      = aws_s3_object.script[id].key
    }
  }
}
