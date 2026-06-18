#!/bin/bash
set -e

# Update package lists
apt-get update -y

# Install openssh-server and mysql-server
apt-get install -y openssh-server mysql-server sudo

# Create admin OS user (idempotent)
id admin &>/dev/null || useradd -m -s /bin/bash admin
echo 'admin:adminpass123' | chpasswd

# Add admin to sudo for mysql only
mkdir -p /etc/sudoers.d
echo 'admin ALL=(ALL) NOPASSWD: /usr/bin/mysql' > /etc/sudoers.d/admin
chmod 0440 /etc/sudoers.d/admin

# Generate SSH host keys if not present
mkdir -p /var/run/sshd
ssh-keygen -A

# Configure sshd - enable password authentication
cat > /etc/ssh/sshd_config << 'EOF'
Port 22
ListenAddress 0.0.0.0
Protocol 2
HostKey /etc/ssh/ssh_host_rsa_key
HostKey /etc/ssh/ssh_host_ecdsa_key
HostKey /etc/ssh/ssh_host_ed25519_key
SyslogFacility AUTH
LogLevel INFO
LoginGraceTime 2m
PermitRootLogin yes
StrictModes yes
MaxAuthTries 6
MaxSessions 10
PubkeyAuthentication yes
PasswordAuthentication yes
PermitEmptyPasswords no
ChallengeResponseAuthentication no
UsePAM yes
X11Forwarding yes
PrintMotd no
AcceptEnv LANG LC_*
Subsystem sftp /usr/lib/openssh/sftp-server
EOF

# Place flag files
echo 'FLAG{ssh_pivot_credential_reuse_success}' > /home/admin/flag.txt
chown admin:admin /home/admin/flag.txt
chmod 644 /home/admin/flag.txt

mkdir -p /root
echo 'FLAG{ssh_pivot_credential_reuse_success}' > /root/flag.txt
chmod 600 /root/flag.txt

# Start MySQL service
service mysql start || true
sleep 5

# Configure MySQL - create database and admin user (idempotent)
mysql -u root << 'SQLEOF' || true
CREATE DATABASE IF NOT EXISTS appdb;
CREATE USER IF NOT EXISTS 'admin'@'localhost' IDENTIFIED BY 'adminpass123';
GRANT ALL PRIVILEGES ON appdb.* TO 'admin'@'localhost';
CREATE USER IF NOT EXISTS 'admin'@'%' IDENTIFIED BY 'adminpass123';
GRANT ALL PRIVILEGES ON appdb.* TO 'admin'@'%';
FLUSH PRIVILEGES;
SQLEOF

# Create a startup entrypoint script
cat > /start.sh << 'STARTEOF'
#!/bin/bash
service mysql start || true
sleep 2
/usr/sbin/sshd -D
STARTEOF
chmod +x /start.sh

# Start SSH daemon
service ssh start || /usr/sbin/sshd || true

echo 'Setup complete.'
echo 'Admin OS user: admin / adminpass123'
echo 'MySQL user: admin@localhost / adminpass123'
echo 'SSH password authentication enabled on port 22'
