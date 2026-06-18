#!/bin/bash
set -e

# Update package lists
apt-get update -y

# Install OpenSSH server
apt-get install -y openssh-server

# Create the low-privilege user 'guest' with weak password 'guest123'
if ! id -u guest &>/dev/null; then
    useradd -m -s /bin/bash guest
fi
echo 'guest:guest123' | chpasswd

# Configure SSH server
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

# Place a CTF-style flag in guest's home directory
echo 'FLAG{weak_credentials_ssh_compromised}' > /home/guest/flag.txt
chown guest:guest /home/guest/flag.txt
chmod 600 /home/guest/flag.txt

# Create a simple welcome message
cat > /home/guest/.bashrc << 'BASHRC'
export PS1='\u@target:\w\$ '
echo "Welcome, guest!"
BASHRC
chown guest:guest /home/guest/.bashrc

# Ensure /run/sshd directory exists
mkdir -p /run/sshd

# Start SSH service - handle both systemd and non-systemd environments
service ssh stop || true
/usr/sbin/sshd

echo "SSH service configured and started."
echo "User: guest / Password: guest123"
echo "SSH is listening on port 22"
