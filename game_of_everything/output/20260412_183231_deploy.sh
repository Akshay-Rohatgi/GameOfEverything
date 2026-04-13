#!/bin/bash
set -e
# --- install_package ---
# Install samba package required for insecure share configuration
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y samba

# --- install_package ---
# Install zip package required for sensitive_file_in_share to function at runtime
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y zip

# --- install_package ---
# Install openssh-server required for SSH password login to function at runtime
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y openssh-server

# --- samba_insecure_share ---
# Create the share directory and set world-readable/writable permissions for anonymous access
mkdir -p /srv/share/backups
chmod 777 /srv/share/backups

# Append anonymous-accessible Samba share configuration to smb.conf
cat >> /etc/samba/smb.conf << 'EOF'

[backups]
   path = /srv/share/backups
   browseable = yes
   guest ok = yes
   read only = no
   create mask = 0755
EOF

# Restart Samba service (Docker-safe: use service instead of systemctl)
if [ -d /run/systemd/system ]; then
  systemctl daemon-reload && systemctl restart smbd
else
  service smbd restart
fi