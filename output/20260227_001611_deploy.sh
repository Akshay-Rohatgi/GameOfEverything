#!/bin/bash
set -e
# --- install_package ---
# Install samba non-interactively — required for the insecure Samba share atom
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y samba

# --- install_package ---
# Install openssh-server non-interactively — required for SSH login by the created user
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y openssh-server

# --- install_package ---
# Install zip non-interactively — required to create the backup zip credential artifact
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y zip

# --- samba_insecure_share ---
# Create the backup share directory — world-accessible for anonymous enumeration
mkdir -p /srv/samba/backup
chmod 777 /srv/samba/backup

# Append an anonymous, world-readable/writable Samba share configuration
# This exposes the backup directory without authentication, enabling credential discovery
cat >> /etc/samba/smb.conf << 'EOF'

[backup]
   path = /srv/samba/backup
   browseable = yes
   guest ok = yes
   read only = no
   create mask = 0755
EOF

# Restart Samba to apply the new insecure share configuration
systemctl restart smbd

# --- create_user ---
# Create the 'operator' user with a home directory and bash shell
# This user's credentials are stored in the Samba share backup zip, acting as the SSH pivot point
useradd -m -s /bin/bash operator

# Set a weak, guessable password matching the credential artifact on the share
echo "operator:operator123" | chpasswd

# Ensure the home directory has standard permissions
chmod 755 /home/operator

# --- sensitive_file ---
# Create a plaintext credentials file to be zipped — contains operator's username and password
# This acts as the credential leak artifact discoverable via the anonymous Samba share
mkdir -p /srv/samba/backup
printf 'username=operator\npassword=operator123' > /tmp/credentials.txt

# Package the credentials file into a zip archive and place it on the Samba share
zip /srv/samba/backup/backup.zip /tmp/credentials.txt

# Remove the plaintext temporary file
rm -f /tmp/credentials.txt

# Set world-readable permissions on the zip so any anonymous guest can retrieve it
chmod 777 /srv/samba/backup/backup.zip
chown nobody:nogroup /srv/samba/backup/backup.zip

# --- set_suid ---
# Ensure /bin/bash is owned by root — prerequisite for SUID escalation
chown root:root /bin/bash

# Set the SUID bit on /bin/bash so a low-privileged user (operator) can execute
# it with root effective privileges, enabling full privilege escalation post-SSH
chmod u+s /bin/bash