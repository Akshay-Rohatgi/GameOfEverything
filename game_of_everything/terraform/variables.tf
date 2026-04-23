variable "scenario_name" {
  description = "Human-readable scenario name (used for tagging)"
  type        = string
}

variable "run_id" {
  description = "GoE run ID (timestamp-based, used for tagging and state isolation)"
  type        = string
}

variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "vpc_cidr" {
  description = "CIDR block for the scenario VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "attacker_cidr" {
  description = "CIDR allowed SSH/admin access to boxes. Set to your IP/32."
  type        = string
  # No default — forces explicit opt-in for security
}

variable "instance_type" {
  description = "EC2 instance type for all boxes"
  type        = string
  default     = "t3.small"
}

variable "boxes" {
  description = "List of box definitions from GoE topology"
  type = list(object({
    box_id   = string
    hostname = string
    role     = string
    services = list(string) # e.g. ["ssh:22", "http:80"]
    public   = bool         # true = public subnet, false = private
  }))
}

variable "ttl_hours" {
  description = "Auto-destroy TTL in hours (0 = no auto-destroy)"
  type        = number
  default     = 4
}
