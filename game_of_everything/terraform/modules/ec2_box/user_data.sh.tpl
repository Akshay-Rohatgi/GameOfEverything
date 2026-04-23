#!/bin/bash
set -euo pipefail

# Cloud-init user data for GoE EC2 box: ${box_id}
# Sets hostname, installs SSM agent, and prepares for deploy script execution.

hostnamectl set-hostname "${hostname}"

# --- Install SSM Agent (for RunCommand-based provisioning) ---
apt-get update -qq
apt-get install -y -qq snapd
snap install amazon-ssm-agent --classic
systemctl enable snap.amazon-ssm-agent.amazon-ssm-agent.service
systemctl start snap.amazon-ssm-agent.amazon-ssm-agent.service

# --- Signal readiness ---
mkdir -p /var/lib/goe
echo '{"box_id": "${box_id}", "ready": true}' > /var/lib/goe/status.json
