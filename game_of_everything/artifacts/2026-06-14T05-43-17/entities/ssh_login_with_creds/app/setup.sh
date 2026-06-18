#!/bin/bash
set -e

# Install OpenSSH server
apt-get update -y
apt-get install -y openssh-server

# Ensure SSH runtime directory exists
mkdir -p /var/run/sshd

# Configure SSH to allow password authentication
# Remove any existing directives and append clean config
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
sed -i 's/^#\?UsePAM.*/UsePAM yes/' /etc/ssh/sshd_config
sed -i 's/^#\?ChallengeResponseAuthentication.*/ChallengeResponseAuthentication no/' /etc/ssh/sshd_config

# Ensure PasswordAuthentication line exists
grep -q '^PasswordAuthentication' /etc/ssh/sshd_config || echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config

# Ensure KbdInteractiveAuthentication is not blocking logins
sed -i 's/^#\?KbdInteractiveAuthentication.*/KbdInteractiveAuthentication no/' /etc/ssh/sshd_config

# Create user 'dbuser' with bash shell (idempotent)
useradd -m -s /bin/bash dbuser 2>/dev/null || true

# Set password
echo 'dbuser:SuperSecret123' | chpasswd

# Place flag file
mkdir -p /home/dbuser
echo 'FLAG{ssh_pivot_successful_via_sqli_credentials}' > /home/dbuser/flag.txt
chown dbuser:dbuser /home/dbuser/flag.txt
chmod 644 /home/dbuser/flag.txt

# Generate SSH host keys if not present
ssh-keygen -A

# Start SSH service (try systemctl first, fall back to direct invocation)
service ssh stop 2>/dev/null || true
killall sshd 2>/dev/null || true
/usr/sbin/sshd

echo 'SSH setup complete. User dbuser created with password SuperSecret123'
