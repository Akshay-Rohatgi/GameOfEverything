#!/bin/bash
set -e

# Update package list
apt-get update -y

# Install openssh-server
apt-get install -y openssh-server

# Create low-privilege user (ignore error if already exists)
useradd -m -s /bin/bash lowpriv || true

# Set weak password
echo 'lowpriv:password123' | chpasswd

# Configure SSH to allow password authentication
# Remove any existing PasswordAuthentication lines and add a clean one
sed -i '/^PasswordAuthentication/d' /etc/ssh/sshd_config
sed -i '/^#PasswordAuthentication/d' /etc/ssh/sshd_config
echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config

# Disable root login to keep it realistic
sed -i '/^PermitRootLogin/d' /etc/ssh/sshd_config
sed -i '/^#PermitRootLogin/d' /etc/ssh/sshd_config
echo 'PermitRootLogin no' >> /etc/ssh/sshd_config

# Disable challenge-response authentication
sed -i '/^ChallengeResponseAuthentication/d' /etc/ssh/sshd_config
sed -i '/^#ChallengeResponseAuthentication/d' /etc/ssh/sshd_config
echo 'ChallengeResponseAuthentication no' >> /etc/ssh/sshd_config

# Ensure KbdInteractiveAuthentication is also set for newer OpenSSH
sed -i '/^KbdInteractiveAuthentication/d' /etc/ssh/sshd_config
echo 'KbdInteractiveAuthentication no' >> /etc/ssh/sshd_config

# Ensure UsePAM is enabled
sed -i '/^UsePAM/d' /etc/ssh/sshd_config
sed -i '/^#UsePAM/d' /etc/ssh/sshd_config
echo 'UsePAM yes' >> /etc/ssh/sshd_config

# Create SSH host keys if they don't exist
mkdir -p /var/run/sshd
ssh-keygen -A

# Start SSH service in foreground
exec /usr/sbin/sshd -D
