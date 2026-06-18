#!/bin/bash
set -e
# --- weak_ssh_credentials ---

# Update package list
apt-get update -y

# Install openssh-server and sshpass
apt-get install -y openssh-server sshpass

# Create the low-privilege user with bash shell (ignore error if already exists)
useradd -m -s /bin/bash lowpriv || true

# Set weak password
echo 'lowpriv:password123' | chpasswd

# Configure sshd to allow password authentication
sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config

# Ensure PasswordAuthentication is present if not already
grep -q '^PasswordAuthentication' /etc/ssh/sshd_config || echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config

# Ensure PermitRootLogin is present if not already
grep -q '^PermitRootLogin' /etc/ssh/sshd_config || echo 'PermitRootLogin no' >> /etc/ssh/sshd_config

# Create privilege separation directory if needed
mkdir -p /run/sshd

# Generate host keys if not present
ssh-keygen -A

# Start the SSH service (try service command, fall back to direct sshd)
service ssh start || service sshd start || /usr/sbin/sshd

echo 'SSH setup complete. User lowpriv created with password password123'
