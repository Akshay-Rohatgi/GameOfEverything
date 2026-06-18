#!/bin/bash
set -e

# Update package lists
apt-get update -y

# Install OpenSSH server and sshpass
apt-get install -y openssh-server sshpass

# Create the admin user with a bash shell and home directory (idempotent)
id -u admin > /dev/null 2>&1 || useradd -m -s /bin/bash admin

# Set the password for admin
echo 'admin:S3cur3P@ssw0rd' | chpasswd

# Configure SSH to allow password authentication
# Remove any existing PasswordAuthentication lines and add a clean one
sed -i '/^PasswordAuthentication/d' /etc/ssh/sshd_config
sed -i '/^#PasswordAuthentication/d' /etc/ssh/sshd_config
echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config

# Set PermitRootLogin to no
sed -i '/^PermitRootLogin/d' /etc/ssh/sshd_config
sed -i '/^#PermitRootLogin/d' /etc/ssh/sshd_config
echo 'PermitRootLogin no' >> /etc/ssh/sshd_config

# Ensure UsePAM is yes for password auth
sed -i '/^UsePAM/d' /etc/ssh/sshd_config
sed -i '/^#UsePAM/d' /etc/ssh/sshd_config
echo 'UsePAM yes' >> /etc/ssh/sshd_config

# Create privilege separation directory if it doesn't exist
mkdir -p /run/sshd
chmod 755 /run/sshd

# Generate host keys if they don't exist
ssh-keygen -A

# Start SSH service
service ssh start || /usr/sbin/sshd -D &

echo 'SSH setup complete. admin user created with password authentication enabled.'
