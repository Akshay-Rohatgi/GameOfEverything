variable "aws_region" {
  type = string
}

variable "run_id" {
  type = string
}

variable "instance_type" {
  type    = string
  default = "t3.small"
}

variable "attacker_cidr" {
  type        = string
  description = "Operator CIDR allowed to reach declared exposed ports"
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "systems" {
  type = map(object({
    hostname       = string
    public         = bool
    exposed_ports  = list(number)
    internal_ports = list(number)
    script_path    = string
  }))
}
