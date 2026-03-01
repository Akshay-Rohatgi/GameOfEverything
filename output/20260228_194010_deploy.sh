#!/bin/bash
set -e
# --- install_package ---
# Install samba non-interactively — required for the insecure share atom
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y samba

# --- install_package ---
# Install zip non-interactively — required for creating the backup zip sensitive file
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y zip

# --- install_package ---
# Install openssh-server non-interactively — required for SSH access via created user
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y openssh-server

# --- samba_insecure_share ---
# Create the share directory and set world-readable/writable permissions
mkdir -p /srv/samba/backup
chmod 777 /srv/samba/backup

# Append anonymous, world-readable Samba share configuration
# This allows unauthenticated guest access for attacker enumeration
cat >> /etc/samba/smb.conf << 'EOF'

[backup]
   path = /srv/samba/backup
   browseable = yes
   guest ok = yes
   read only = no
   create mask = 0755
EOF

# Restart Samba to apply the new insecure share configuration
service smbd restart || true

# --- sensitive_file ---
# Create a plaintext credentials file to be zipped — simulates a sloppy developer backup
# The credentials correspond to the 'operator' system user created later
mkdir -p /tmp/backup_staging
cat > /tmp/backup_staging/credentials.txt << 'EOF'
username: operator
password: operator123
EOF

# Package the credentials file into a zip archive at the target Samba share path
# World-readable permissions ensure any guest accessing the share can retrieve it
zip /srv/samba/backup/backup.zip /tmp/backup_staging/credentials.txt
chmod 777 /srv/samba/backup/backup.zip

# Clean up staging directory
rm -rf /tmp/backup_staging