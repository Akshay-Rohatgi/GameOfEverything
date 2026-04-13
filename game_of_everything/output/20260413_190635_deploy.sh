#!/bin/bash
set -e
# --- install_package ---
# Install samba package required for insecure share configuration
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y samba

# --- install_package ---
# Install zip package required for creating archive in sensitive file share
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y zip

# --- install_package ---
# Install openssh-server package required for SSH user account access
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y openssh-server

# --- ssh_user_account ---
# Create local user 'dwalker' with a known password for SSH lateral movement
useradd -m -s /bin/bash dwalker
echo 'dwalker:Harvest7742!' | chpasswd

# Ensure the home directory has standard permissions
chmod 755 /home/dwalker

# Configure SSH to allow password authentication and start the service
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config

# Ensure SSH host keys exist
ssh-keygen -A

# Start SSH service (Docker-safe: no systemd)
if [ -d /run/systemd/system ]; then
  systemctl daemon-reload && systemctl enable ssh && systemctl start ssh
else
  service ssh start
fi

# --- samba_insecure_share ---
# Create the share directory and make it world-accessible for anonymous enumeration
mkdir -p /srv/backups
chmod 777 /srv/backups

# Append insecure share configuration block to smb.conf (guest ok, no auth required)
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

# --- sensitive_file_in_share ---
# Create a plaintext credentials file for user 'dwalker' inside the share directory
mkdir -p /srv/backups
cat > /srv/backups/credentials.txt << 'EOF'
# Backup credentials - DO NOT SHARE
username: dwalker
password: Harvest7742!
EOF

# Set weak permissions so any guest/anonymous user can read it
chmod 777 /srv/backups/credentials.txt
chown nobody:nogroup /srv/backups/credentials.txt

# Package the credentials file into a ZIP archive inside the share
cd /srv/backups && zip backups.zip credentials.txt

# Ensure the archive is world-readable
chmod 777 /srv/backups/backups.zip

# --- suid_bash ---
# Set the SUID bit on /bin/bash to allow any local user to obtain a root shell via 'bash -p'
# This is a classic privilege escalation vector left behind by a careless administrator
chown root:root /bin/bash
chmod u+s /bin/bash