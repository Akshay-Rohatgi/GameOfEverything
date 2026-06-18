#!/bin/bash
set -e

# Update package lists
apt-get update -y

# Install openssh-server and sshpass
apt-get install -y openssh-server sshpass

# Create admin user with weak password
useradd -m -s /bin/bash admin 2>/dev/null || true
echo 'admin:password' | chpasswd

# Ensure sshd_config has PasswordAuthentication yes
if grep -q '^PasswordAuthentication' /etc/ssh/sshd_config; then
  sed -i 's/^PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
else
  echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config
fi

# Ensure PermitRootLogin is set
if grep -q '^PermitRootLogin' /etc/ssh/sshd_config; then
  sed -i 's/^PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
else
  echo 'PermitRootLogin no' >> /etc/ssh/sshd_config
fi

# Allow many auth tries (no lockout)
grep -q '^MaxAuthTries' /etc/ssh/sshd_config && sed -i 's/^MaxAuthTries.*/MaxAuthTries 100/' /etc/ssh/sshd_config || echo 'MaxAuthTries 100' >> /etc/ssh/sshd_config

# Ensure UsePAM is enabled
if grep -q '^UsePAM' /etc/ssh/sshd_config; then
  sed -i 's/^UsePAM.*/UsePAM yes/' /etc/ssh/sshd_config
else
  echo 'UsePAM yes' >> /etc/ssh/sshd_config
fi

# Remove fail2ban if installed
apt-get remove -y fail2ban 2>/dev/null || true

# Disable any UFW firewall rules
ufw disable 2>/dev/null || true

# Clear iptables rules
iptables -F 2>/dev/null || true

# Generate SSH host keys if they don't exist
ssh-keygen -A

# Ensure /var/run/sshd directory exists
mkdir -p /var/run/sshd

# Start SSH service
service ssh start 2>/dev/null || /usr/sbin/sshd || true

echo 'SSH weak password setup complete. User: admin, Password: password'
