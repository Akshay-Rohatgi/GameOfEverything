#!/bin/bash
set -e
# --- custom_app/ssti_jinja2 ---
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-flask python3-pip sqlite3 curl

mkdir -p /opt/webapp
cp /tmp/goe_app/app.py /opt/webapp/app.py
cp /tmp/goe_app/schema.sql /opt/webapp/schema.sql
cp /tmp/goe_app/seed.sql /opt/webapp/seed.sql
cp /tmp/goe_app/setup_db.sh /opt/webapp/setup_db.sh

bash /tmp/goe_app/setup_db.sh

cat > /etc/systemd/system/webapp.service << 'EOF'
[Unit]
Description=Vulnerable Web App
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/webapp/app.py
Restart=always
WorkingDirectory=/opt/webapp

[Install]
WantedBy=multi-user.target
EOF

if [ -d /run/systemd/system ]; then
  systemctl daemon-reload
  systemctl enable webapp
  systemctl start webapp
else
  cd /opt/webapp && nohup python3 app.py > /var/log/webapp.log 2>&1 &
  sleep 2
fi

# --- install_package ---
# Install openssh-server non-interactively — required for SSH-based initial access
DEBIAN_FRONTEND=noninteractive apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y openssh-server

# --- weak_service_password ---
# Ensure SSH password authentication is enabled so 'wikiuser' can log in with the known weak password
sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config

# Ensure UsePAM is enabled (required for chpasswd-set passwords to work with SSH)
sed -i 's/^#*UsePAM.*/UsePAM yes/' /etc/ssh/sshd_config

# Confirm the weak password is set for wikiuser (idempotent)
echo "wikiuser:Redwood#2021" | chpasswd

# Install sshpass if not already present (needed for testing)
dpkg -l sshpass 2>/dev/null | grep -q '^ii' || apt-get install -y sshpass

# Start (or restart) the SSH service — use service command for Docker compatibility
if [ -d /run/systemd/system ]; then
  systemctl daemon-reload && systemctl enable ssh && systemctl restart ssh
else
  service ssh restart || service ssh start
fi

# --- sudoers_no_passwd ---
# Ensure sudo is installed
DEBIAN_FRONTEND=noninteractive apt-get install -y sudo

# Grant 'wikiuser' unconditional NOPASSWD sudo for ALL commands — full privilege escalation path
echo "wikiuser ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/wikiuser

# Set mandatory 440 permissions on the drop-in sudoers file
chmod 440 /etc/sudoers.d/wikiuser