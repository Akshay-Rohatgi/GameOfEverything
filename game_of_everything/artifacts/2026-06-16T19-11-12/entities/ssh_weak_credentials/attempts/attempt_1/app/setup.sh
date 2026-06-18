#!/bin/bash
set -e

# Update package lists
apt-get update -y

# Install OpenSSH server
apt-get install -y openssh-server

# Create low-privilege user with weak password (idempotent)
if ! id lowpriv &>/dev/null; then
    useradd -m -s /bin/bash lowpriv
fi
echo "lowpriv:password123" | chpasswd

# Configure SSH to allow password authentication
mkdir -p /etc/ssh

# Write sshd_config with weak settings (no lockout, password auth enabled)
cat > /etc/ssh/sshd_config << 'EOF'
# SSH Server Configuration
Port 22
AddressFamily any
ListenAddress 0.0.0.0
ListenAddress ::

HostKey /etc/ssh/ssh_host_rsa_key
HostKey /etc/ssh/ssh_host_ecdsa_key
HostKey /etc/ssh/ssh_host_ed25519_key

SyslogFacility AUTH
LogLevel INFO

LoginGraceTime 2m
PermitRootLogin no
StrictModes yes
MaxAuthTries 999
MaxSessions 100

PubkeyAuthentication yes
AuthorizedKeysFile .ssh/authorized_keys

PasswordAuthentication yes
PermitEmptyPasswords no
ChallengeResponseAuthentication no

UsePAM yes

X11Forwarding yes
PrintMotd no

AcceptEnv LANG LC_*
Subsystem sftp /usr/lib/openssh/sftp-server
EOF

# Ensure SSH host keys are generated
ssh-keygen -A

# Disable UFW if present to avoid blocking port 22
if command -v ufw &>/dev/null; then
    ufw disable || true
fi

# Stop and disable fail2ban if present
if command -v fail2ban-client &>/dev/null; then
    systemctl stop fail2ban || true
    systemctl disable fail2ban || true
fi

# Ensure /var/run/sshd exists
mkdir -p /var/run/sshd

# Kill any existing sshd process before restarting
killall sshd || true
sleep 1

# Start sshd in the background (Docker-compatible, no systemd)
/usr/sbin/sshd -D &

echo "SSH setup complete."
echo "User 'lowpriv' created with password 'password123'"
echo "SSH is listening on port 22 with PasswordAuthentication yes and no lockout policy"
