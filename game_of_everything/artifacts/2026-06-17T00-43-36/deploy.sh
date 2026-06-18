#!/bin/bash
set -e
# --- ssh_weak_credentials ---

# Update package lists
apt-get update -y

# Install openssh-server and sshpass
apt-get install -y openssh-server sshpass

# Create the low-privilege user 'appuser' with weak password (idempotent)
id appuser &>/dev/null || useradd -m -s /bin/bash appuser
echo 'appuser:password123' | chpasswd

# Configure SSH
mkdir -p /etc/ssh

# Write sshd_config with password authentication explicitly enabled
cat > /etc/ssh/sshd_config << 'EOF'
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
MaxAuthTries 6
MaxSessions 10

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

# Generate SSH host keys if they don't exist
ssh-keygen -A

# Ensure the SSH runtime directory exists
mkdir -p /var/run/sshd

# Start SSH service (try service command first, fall back to direct invocation)
service ssh start || service sshd start || /usr/sbin/sshd || true

echo "SSH setup complete. User 'appuser' created with password 'password123'"
echo "SSH is listening on port 22 with password authentication enabled"

# --- sudo_privesc_root ---

# Ensure 'appuser' exists (already created by SSH entity, but create if not present)
if ! id -u appuser &>/dev/null; then
    useradd -m -s /bin/bash appuser
fi

# Ensure sudoers.d directory exists
mkdir -p /etc/sudoers.d

# Write the sudoers drop-in file granting appuser NOPASSWD access to /bin/bash
cat > /etc/sudoers.d/appuser << 'EOF'
appuser ALL=(root) NOPASSWD: /bin/bash
EOF

# Set correct permissions on the drop-in file
chmod 0440 /etc/sudoers.d/appuser

# Ensure sudo package is installed
apt-get install -y sudo 2>/dev/null || true

# Validate the sudoers configuration
visudo -c

echo "Sudo misconfiguration setup complete."
echo "User 'appuser' can now run: sudo /bin/bash"
