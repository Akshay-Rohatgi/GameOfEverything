#!/bin/bash
set -e

# Update package list
apt-get update -y

# Install openssh-server and sshpass
apt-get install -y openssh-server sshpass

# Create the admin user with a home directory and bash shell (idempotent)
useradd -m -s /bin/bash -u 1001 admin 2>/dev/null || true

# Set the password for admin user
echo 'admin:S3cur3P@ssw0rd' | chpasswd

# Ensure SSH host keys exist
ssh-keygen -A

# Configure sshd_config - password authentication explicitly enabled
cat > /etc/ssh/sshd_config <<'EOF'
Port 22
ListenAddress 0.0.0.0
Protocol 2

HostKey /etc/ssh/ssh_host_rsa_key
HostKey /etc/ssh/ssh_host_ecdsa_key
HostKey /etc/ssh/ssh_host_ed25519_key

SyslogFacility AUTH
LogLevel INFO

LoginGraceTime 2m
PermitRootLogin no
StrictModes yes
MaxAuthTries 6
MaxSessions 10

PasswordAuthentication yes
PermitEmptyPasswords no
ChallengeResponseAuthentication no

UsePAM yes

X11Forwarding no
PrintMotd no

AcceptEnv LANG LC_*

Subsystem sftp /usr/lib/openssh/sftp-server
EOF

# Create privilege separation directory
mkdir -p /run/sshd
chmod 0755 /run/sshd

# Kill any existing sshd instances (idempotent)
killall sshd 2>/dev/null || true
sleep 1

# Start SSH daemon in background
/usr/sbin/sshd -D &

echo "SSH setup complete. Admin user created with password authentication enabled."
echo "SSH daemon is running on port 22."

# Keep the script running to prevent container exit
wait
