#!/bin/bash
set -e
# --- custom_app/ssti_jinja2 ---
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-flask python3-pip mariadb-server curl

service mariadb start
sleep 2

mkdir -p /opt/webapp
cp /tmp/goe_app/app.py /opt/webapp/app.py

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