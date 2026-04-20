variable "run_id" {
  type = string
}

variable "box_id" {
  type = string
}

variable "hostname" {
  type = string
}

variable "role" {
  type = string
}

variable "services" {
  description = "List of service:port strings (e.g. ['ssh:22', 'http:80'])"
  type        = list(string)
  default     = []
}

variable "instance_type" {
  type    = string
  default = "t3.small"
}

variable "attacker_cidr" {
  description = "CIDR allowed for SSH/admin access"
  type        = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_id" {
  type = string
}

variable "ssm_instance_profile_name" {
  type = string
}
