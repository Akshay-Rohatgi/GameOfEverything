#!/bin/bash
set -e
# --- install_package ---
# Install samba package non-interactively — required for samba_insecure_share
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y samba

# --- install_package ---
# Install zip package non-interactively — required for sensitive_file_in_archive
export DEBIAN_FRONTEND=noninteractive
apt-get install -y zip

# --- install_package ---
# Install openssh-server non-interactively — required for ssh_password_login
export DEBIAN_FRONTEND=noninteractive
apt-get install -y openssh-server

# --- samba_insecure_share ---
# Create the directory for the 'backups' Samba share and make it world-accessible
mkdir -p /srv/share/backups
chmod 777 /srv/share/backups

# Append the insecure anonymous share configuration to smb.conf
cat >> /etc/samba/smb.conf << 'EOF'

[backups]
   path = /srv/share/backups
   browseable = yes
   guest ok = yes
   read only = no
   create mask = 0755
EOF

# Restart Samba service (Docker-safe: no systemd)
if [ -d /run/systemd/system ]; then
  systemctl daemon-reload && systemctl restart smbd
else
  service smbd restart
fi

# --- sensitive_file_in_archive ---
# Create a plaintext credentials file with the discovered username and password
mkdir -p /srv/share/backups
cat > /tmp/credentials.txt << 'EOF'
username: dthompson
password: Harbour.2023!
EOF

# Package the credentials file into a zip archive and place it on the Samba share
cd /tmp && zip backups.zip credentials.txt
mv /tmp/backups.zip /srv/share/backups/backups.zip

# Ensure the archive is world-readable so unauthenticated share access can retrieve it
chmod 644 /srv/share/backups/backups.zip
rm -f /tmp/credentials.txt

# --- ssh_password_login ---
# Create the OS user 'dthompson' with a home directory and bash shell
useradd -m -s /bin/bash dthompson
# Set the discovered password for the user to enable SSH password-based login
echo 'dthompson:Harbour.2023!' | chpasswd

# Configure SSH to permit password authentication and allow this user
sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
# Ensure SSH host keys exist (needed when openssh-server is freshly installed in Docker)
ssh-keygen -A

# Start SSH service (Docker-safe: no systemd)
if [ -d /run/systemd/system ]; then
  systemctl enable ssh && systemctl start ssh
else
  service ssh start
fi

# --- suid_bash_privesc ---
# Set SUID bit on /bin/bash owned by root — allows any local user to spawn a root-privileged shell
chown root:root /bin/bash
chmod u+s /bin/bash