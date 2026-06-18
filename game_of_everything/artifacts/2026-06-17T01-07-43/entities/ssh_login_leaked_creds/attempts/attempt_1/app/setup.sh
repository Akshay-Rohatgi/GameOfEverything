#!/bin/bash
set -e

# Install required packages
apt-get update -y
apt-get install -y openssh-server sshpass

# Create low-privilege user sysadmin if not exists
if ! id -u sysadmin >/dev/null 2>&1; then
    useradd -m -s /bin/bash sysadmin
fi
echo 'sysadmin:Sup3rS3cur3SSH!' | chpasswd

# Ensure sysadmin is NOT in sudo group
gpasswd -d sysadmin sudo 2>/dev/null || true

# Place a flag file in the home directory
mkdir -p /home/sysadmin
echo 'FLAG{ssh_login_via_sqli_credential_leak}' > /home/sysadmin/flag.txt
chown sysadmin:sysadmin /home/sysadmin/flag.txt
chmod 644 /home/sysadmin/flag.txt

# Configure sshd
mkdir -p /var/run/sshd

# Write a clean sshd_config with password authentication enabled
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
PubkeyAuthentication yes
PasswordAuthentication yes
ChallengeResponseAuthentication no
UsePAM yes
X11Forwarding yes
PrintMotd no
AcceptEnv LANG LC_*
Subsystem sftp /usr/lib/openssh/sftp-server
EOF

# Generate SSH host keys if not present
ssh-keygen -A

# Start SSH service (handle both systemd and non-systemd environments)
service ssh start || service sshd start || /usr/sbin/sshd

echo 'SSH setup complete. sysadmin user created with password authentication enabled.'
