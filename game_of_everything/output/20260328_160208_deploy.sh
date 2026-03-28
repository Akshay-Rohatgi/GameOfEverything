#!/bin/bash
set -e
# --- custom_app/sqli_union ---
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y apache2 php libapache2-mod-php php-mysql php-sqlite3 mariadb-server
service mariadb start
sleep 2

cp /tmp/goe_app/app.php /var/www/html/app.php
chmod 644 /var/www/html/app.php

bash /tmp/goe_app/setup_db.sh

service apache2 start
sleep 1

# --- install_package ---
# Install openssh-server non-interactively — required for SSH password-based login
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y openssh-server

# --- weak_service_password ---
# Install sshpass for SSH password authentication testing
dpkg -l sshpass 2>/dev/null | grep -q '^ii' || apt-get install -y sshpass

# Configure SSH to allow password-based authentication — enables credential brute-force/spray on port 22
sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
# Ensure UsePAM is enabled so chpasswd-set passwords are accepted
sed -i 's/^#*UsePAM.*/UsePAM yes/' /etc/ssh/sshd_config
# Ensure the user account exists
id dmartin 2>/dev/null || useradd -m -s /bin/bash dmartin
# Confirm the user account has the weak password set
echo "dmartin:Harbour7west!" | chpasswd
# Start (or restart) the SSH service so changes take effect
service ssh restart || service ssh start || true

# --- set_suid ---
# Ensure /usr/bin/find is owned by root — prerequisite for SUID escalation
chown root:root /usr/bin/find
# Set the SUID bit on /usr/bin/find — allows dmartin to execute find as root for privilege escalation
chmod u+s /usr/bin/find