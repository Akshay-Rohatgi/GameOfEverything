#!/bin/bash
set -e
# --- install_package ---
# Install samba package non-interactively — required for insecure share setup
DEBIAN_FRONTEND=noninteractive apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y samba

# --- install_package ---
# Install zip package non-interactively — required for creating the sensitive archive
DEBIAN_FRONTEND=noninteractive apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y zip

# --- install_package ---
# Install openssh-server non-interactively — required for SSH password auth
DEBIAN_FRONTEND=noninteractive apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y openssh-server

# --- samba_insecure_share ---
# Create the backups share directory and make it world-accessible for anonymous enumeration
mkdir -p /srv/backups
chmod 777 /srv/backups

# Append the insecure 'backups' share block to smb.conf — guest ok enables unauthenticated access
cat >> /etc/samba/smb.conf << 'EOF'

[backups]
   path = /srv/backups
   browseable = yes
   guest ok = yes
   read only = no
   create mask = 0755
EOF

# Restart Samba service (Docker-safe: no systemd)
if [ -d /run/systemd/system ]; then
  systemctl daemon-reload && systemctl enable smbd && systemctl restart smbd
else
  service smbd restart
fi

# --- sensitive_file ---
# Create a plaintext credentials file for dparker — simulates leaked credentials accessible without authentication
mkdir -p /srv/backups
cat > /srv/backups/credentials.txt << 'EOF'
username: dparker
password: Heron.Lake82
EOF

# Set world-readable permissions so any unauthenticated user via the Samba share can read it
chmod 644 /srv/backups/credentials.txt

# Package the credentials file into a zip archive to simulate a backup artifact
cd /srv/backups && zip backups.zip credentials.txt

# Ensure the archive is world-readable as well
chmod 644 /srv/backups/backups.zip

# --- ssh_password_auth ---
# Create the local user dparker with a home directory and bash shell
useradd -m -s /bin/bash dparker

# Set dparker's password to the leaked credential — enables direct SSH login with the found credentials
echo 'dparker:Heron.Lake82' | chpasswd

# Enable SSH password authentication in sshd_config — required for password-based shell access
sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
sed -i 's/^#*PermitEmptyPasswords.*/PermitEmptyPasswords no/' /etc/ssh/sshd_config

# Ensure the SSH host keys are generated (needed in Docker where they may not exist)
ssh-keygen -A

# Start SSH service (Docker-safe: no systemd)
if [ -d /run/systemd/system ]; then
  systemctl daemon-reload && systemctl enable ssh && systemctl start ssh
else
  service ssh start
fi

# --- suid_bash_privesc ---
# Ensure /bin/bash is owned by root — prerequisite for SUID privilege escalation
chown root:root /bin/bash

# Set the SUID bit on /bin/bash — allows dparker to run 'bash -p' and obtain a root-privileged shell
chmod u+s /bin/bash