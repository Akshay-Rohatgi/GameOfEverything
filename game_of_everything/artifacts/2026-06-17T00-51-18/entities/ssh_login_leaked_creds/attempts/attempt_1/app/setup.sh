#!/bin/bash
set -e

# Install OpenSSH server
apt-get update -y
apt-get install -y openssh-server

# Ensure SSH runtime directory exists
mkdir -p /var/run/sshd

# Configure sshd to allow password authentication and disable root login
# Remove any existing conflicting lines and set desired values
sed -i '/^#\?PasswordAuthentication/d' /etc/ssh/sshd_config
sed -i '/^#\?PermitRootLogin/d' /etc/ssh/sshd_config
echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config
echo 'PermitRootLogin no' >> /etc/ssh/sshd_config

# Create low-privilege user sysadmin if not already present
id sysadmin &>/dev/null || useradd -m -s /bin/bash sysadmin

# Set password exactly as leaked via SQL injection
echo 'sysadmin:Sup3rS3cr3tSSH!' | chpasswd

# Generate SSH host keys if not already present
ssh-keygen -A

# Start SSH service
service ssh start || /usr/sbin/sshd || true

echo 'SSH setup complete. User sysadmin created with password Sup3rS3cr3tSSH!'
