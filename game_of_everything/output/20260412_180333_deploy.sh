#!/bin/bash
set -e
# --- install_package ---
# Install samba non-interactively — required for the insecure share atom
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y samba

# --- install_package ---
# Install zip non-interactively — required by sensitive_file to create zip archives
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y zip

# --- install_package ---
# Install openssh-server non-interactively — required for SSH login atom
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y openssh-server

# --- samba_insecure_share ---
# Create the share directory and make it world-accessible for anonymous enumeration
mkdir -p /srv/share/backups
chmod 777 /srv/share/backups

# Append an anonymous, guest-accessible Samba share block to smb.conf
cat >> /etc/samba/smb.conf << 'EOF'

[backups]
   path = /srv/share/backups
   browseable = yes
   guest ok = yes
   read only = no
   create mask = 0755
EOF

# Start the Samba service (Docker-safe: no systemd)
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload && systemctl enable smbd && systemctl restart smbd
else
    service smbd start
fi

# --- sensitive_file ---
# Create a plausible credentials plaintext file disclosing dthompson's OS credentials
mkdir -p /tmp/backup_staging
cat > /tmp/backup_staging/credentials.txt << 'EOF'
# Local OS user credentials — DO NOT DISTRIBUTE
username: dthompson
password: Harbour.2023
EOF

# Package the credentials file into a zip archive and place it on the Samba share
zip /srv/share/backups/backup_credentials.zip /tmp/backup_staging/credentials.txt

# Set weak permissions so any user (including anonymous Samba guest) can read it
chmod 777 /srv/share/backups/backup_credentials.zip
chown nobody:nogroup /srv/share/backups/backup_credentials.zip

# --- ssh_login ---
# Create local OS user dthompson with the disclosed password to enable SSH login
useradd -m -s /bin/bash dthompson
echo 'dthompson:Harbour.2023' | chpasswd

# Ensure the home directory has standard permissions
chmod 755 /home/dthompson

# Configure SSH to allow password authentication
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config

# Create the privilege separation directory required by sshd
mkdir -p /run/sshd

# Start SSH service (Docker-safe: no systemd)
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload && systemctl enable ssh && systemctl start ssh
else
    service ssh start
fi

# --- suid_bash ---
# Set SUID bit on /bin/bash owned by root — allows any local user to run 'bash -p' for an effective root shell
chown root:root /bin/bash
chmod u+s /bin/bash