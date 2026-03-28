#!/bin/bash
set -e
# --- custom_app/sqli_union ---
mkdir -p /tmp/goe_app
cat > /tmp/goe_app/app.py << 'GOE_APP_PY_EOF'
from flask import Flask, request, render_template_string
import sqlite3
import os

app = Flask(__name__)
DB_PATH = '/opt/webapp/app.db'

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head><title>Product Search</title></head>
<body>
<h1>Product Search</h1>
<form method="GET" action="/search">
  <input type="text" name="q" placeholder="Search products..." value="{{ query }}">
  <button type="submit">Search</button>
</form>
{% if results is not none %}
<h2>Results:</h2>
<table border="1">
  <tr><th>Name</th><th>Description</th></tr>
  {% for row in results %}
  <tr><td>{{ row[0] }}</td><td>{{ row[1] }}</td></tr>
  {% endfor %}
</table>
{% endif %}
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, query='', results=None)

@app.route('/search')
def search():
    q = request.args.get('q', '')
    results = []
    try:
        conn = get_db()
        # VULNERABLE: direct string concatenation — no parameterized query
        query = "SELECT name, description FROM products WHERE name LIKE '%" + q + "%'"
        cursor = conn.execute(query)
        results = cursor.fetchall()
        conn.close()
    except Exception as e:
        results = [('Error', str(e))]
    return render_template_string(HTML_TEMPLATE, query=q, results=results)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

GOE_APP_PY_EOF
cat > /tmp/goe_app/schema.sql << 'GOE_SCHEMA_SQL_EOF'
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    password TEXT NOT NULL
);

GOE_SCHEMA_SQL_EOF
cat > /tmp/goe_app/seed.sql << 'GOE_SEED_SQL_EOF'
INSERT INTO products (name, description) VALUES ('Widget A', 'A standard widget for everyday use');
INSERT INTO products (name, description) VALUES ('Widget B', 'An advanced widget with extra features');
INSERT INTO products (name, description) VALUES ('Gadget X', 'A compact gadget for professionals');
INSERT INTO products (name, description) VALUES ('Gadget Y', 'A premium gadget with wireless capability');
INSERT INTO products (name, description) VALUES ('Tool Z', 'A versatile tool for all purposes');

INSERT INTO users (username, password) VALUES ('admin', 'admin123');
INSERT INTO users (username, password) VALUES ('dwalker', 'Sd9#fLp2x!mQ');

GOE_SEED_SQL_EOF
cat > /tmp/goe_app/setup_db.sh << 'GOE_SETUP_DB_SH_EOF'
cd /opt/webapp

# Remove old DB if exists to ensure clean state, then recreate
rm -f /opt/webapp/app.db

# Create schema
sqlite3 /opt/webapp/app.db < /tmp/goe_app/schema.sql

# Seed data
sqlite3 /opt/webapp/app.db < /tmp/goe_app/seed.sql

# Make DB writable by webapp
chmod 666 /opt/webapp/app.db

GOE_SETUP_DB_SH_EOF
chmod +x /tmp/goe_app/setup_db.sh

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-flask python3-pip sqlite3 curl

mkdir -p /opt/webapp
cp /tmp/goe_app/app.py /opt/webapp/app.py
cp /tmp/goe_app/schema.sql /opt/webapp/schema.sql
cp /tmp/goe_app/seed.sql /opt/webapp/seed.sql

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

# Cleanup staging directory
rm -rf /tmp/goe_app

# --- install_package ---
# Install openssh-server non-interactively — required for SSH-based credential attacks
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y openssh-server

# --- weak_service_password ---
# Install sshpass if not already present (needed for testing)
dpkg -l sshpass 2>/dev/null | grep -q '^ii' || DEBIAN_FRONTEND=noninteractive apt-get install -y sshpass

# Configure SSH to allow password-based authentication for user 'dwalker'
# Ensure PasswordAuthentication is enabled in sshd_config
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
# Ensure PubkeyAuthentication is also permitted (not strictly required but realistic)
sed -i 's/^#\?PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config

# Create user dwalker if not already present
id dwalker 2>/dev/null || useradd -m -s /bin/bash dwalker

# Set the weak password for dwalker (idempotent — safe to repeat)
echo "dwalker:Felicity2019!" | chpasswd

# Start (or restart) the SSH service — use service in Docker since systemd is not PID 1
if [ -d /run/systemd/system ]; then
  systemctl daemon-reload && systemctl enable ssh && systemctl restart ssh
else
  service ssh restart || service ssh start
fi

# --- sudoers_no_passwd ---
# Ensure sudo is installed
apt-get install -y sudo
# Grant 'dwalker' unconditional NOPASSWD sudo for ALL commands — full privilege escalation path
echo "dwalker ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/dwalker
# Set correct restrictive permissions on the drop-in sudoers file
chmod 440 /etc/sudoers.d/dwalker