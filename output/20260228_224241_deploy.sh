#!/bin/bash
set -e
# --- install_package ---
# Install samba non-interactively — required for the insecure share atom
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y samba

# --- install_package ---
# Install openssh-server non-interactively — required for SSH login by the created user
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y openssh-server

# --- install_package ---
# Install zip non-interactively — required for creating the backup zip archive
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y zip

# --- samba_insecure_share ---
# Create the backup share directory and make it world-accessible for anonymous enumeration
mkdir -p /srv/samba/backup
chmod 777 /srv/samba/backup

# Append anonymous, world-readable/writable share config to smb.conf
cat >> /etc/samba/smb.conf << 'EOF'

[backup]
   path = /srv/samba/backup
   browseable = yes
   guest ok = yes
   read only = no
   create mask = 0755
EOF

# Restart Samba to apply the new share configuration
service smbd restart || true

# --- create_user ---
# Create the 'operator' user with a home directory and bash shell
# Use -g operator to avoid group conflict (group 'operator' already exists)
id operator 2>/dev/null || useradd -m -s /bin/bash -g operator operator

# Set password using passwd (chpasswd fails due to PAM obscure policy)
echo -e 'operator123\noperator123' | passwd operator

# Ensure SSH password authentication is enabled so the attacker can log in
sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config

# Start the SSH service
service ssh start || true

# --- sensitive_file ---
# Create a plaintext credentials file and package it into a backup zip
# This zip is placed in the anonymous Samba share so it can be retrieved without authentication
mkdir -p /srv/samba/backup

# Write credentials to a temp file, zip it, then clean up the plaintext
printf 'username: operator\npassword: operator123' > /tmp/credentials.txt
zip /srv/samba/backup/backup.zip /tmp/credentials.txt
rm -f /tmp/credentials.txt

# Set world-readable permissions on the zip so any guest can retrieve it
chmod 777 /srv/samba/backup/backup.zip
chown nobody:nogroup /srv/samba/backup/backup.zip

# --- set_suid ---
# Ensure /bin/bash is owned by root and set the SUID bit
# This allows the low-privilege 'operator' user to execute /bin/bash -p and gain a root shell
chown root:root /bin/bash
chmod u+s /bin/bash