#!/bin/bash
set -e

# Update package lists
apt-get update -y

# Install OpenSSH server
apt-get install -y openssh-server

# Create low-privilege user with weak password (idempotent)
if ! id lowuser &>/dev/null; then
    useradd -m -s /bin/bash lowuser
fi
echo 'lowuser:password123' | chpasswd

# Configure SSH to allow password authentication
mkdir -p /etc/ssh

# Write sshd_config with weak settings
cat > /etc/ssh/sshd_config << 'EOF'
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
MaxAuthTries 10
MaxSessions 10
PubkeyAuthentication yes
PasswordAuthentication yes
PermitEmptyPasswords no
ChallengeResponseAuthentication no
UsePAM yes
X11Forwarding yes
PrintMotd no
AcceptEnv LANG LC_*
Subsystem sftp /usr/lib/openssh/sftp-server
EOF

# Generate SSH host keys if they don't exist
ssh-keygen -A

# Remove fail2ban if installed
apt-get remove -y fail2ban 2>/dev/null || true

# Disable PAM password complexity modules
if [ -f /etc/pam.d/common-password ]; then
    sed -i 's/.*pam_pwquality.*//g' /etc/pam.d/common-password
    sed -i 's/.*pam_cracklib.*//g' /etc/pam.d/common-password
fi

# Disable pam_tally2 and pam_faillock if present
if [ -f /etc/pam.d/common-auth ]; then
    sed -i 's/.*pam_tally2.*//g' /etc/pam.d/common-auth
    sed -i 's/.*pam_faillock.*//g' /etc/pam.d/common-auth
fi

# Remove libpam-cracklib if installed
apt-get remove -y libpam-cracklib 2>/dev/null || true

# Ensure /var/run/sshd exists
mkdir -p /var/run/sshd

# Start sshd - handle both systemd and non-systemd (Docker) environments
if command -v systemctl &>/dev/null && systemctl is-system-running &>/dev/null 2>&1; then
    systemctl enable ssh 2>/dev/null || systemctl enable sshd 2>/dev/null || true
    systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || true
else
    # Kill any existing sshd process first (idempotent)
    pkill -f '/usr/sbin/sshd' 2>/dev/null || true
    sleep 1
    /usr/sbin/sshd
fi

echo "SSH setup complete. User 'lowuser' created with password 'password123'"
echo "SSH is listening on port 22 with PasswordAuthentication enabled"
