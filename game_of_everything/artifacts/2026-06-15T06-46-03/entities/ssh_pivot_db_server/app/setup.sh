#!/bin/bash
set -e

# Update package lists
apt-get update -y

# Install openssh-server and sudo
apt-get install -y openssh-server sudo

# Create admin user with home directory and bash shell (idempotent)
id admin >/dev/null 2>&1 || useradd -m -s /bin/bash admin

# Set password for admin user (matching MySQL credentials discovered via SQLi)
echo 'admin:adminpass123' | chpasswd

# Add admin to sudo group
usermod -aG sudo admin

# Allow passwordless sudo for /bin/bash to enable privilege escalation demo
echo 'admin ALL=(ALL) NOPASSWD: /bin/bash' > /etc/sudoers.d/admin
chmod 440 /etc/sudoers.d/admin

# Configure SSH to allow password authentication
mkdir -p /etc/ssh

# Ensure PasswordAuthentication is set to yes
sed -i 's/#PasswordAuthentication yes/PasswordAuthentication yes/' /etc/ssh/sshd_config
sed -i 's/PasswordAuthentication no/PasswordAuthentication yes/' /etc/ssh/sshd_config

# Also explicitly add the setting in case it wasn't present
grep -q '^PasswordAuthentication' /etc/ssh/sshd_config || echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config

# Disable strict host checking and allow root login for demo purposes
grep -q '^PermitRootLogin' /etc/ssh/sshd_config || echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config
grep -q '^UsePAM' /etc/ssh/sshd_config || echo 'UsePAM yes' >> /etc/ssh/sshd_config

# Generate SSH host keys if not present
ssh-keygen -A

# Plant flag files (idempotent)
echo 'FLAG{ssh_pivot_db_server_compromised}' > /home/admin/flag.txt
chown admin:admin /home/admin/flag.txt
chmod 644 /home/admin/flag.txt

mkdir -p /root
echo 'FLAG{root_db_server_owned}' > /root/flag.txt
chmod 600 /root/flag.txt

# Create a startup script to launch sshd
cat > /start.sh << 'EOF'
#!/bin/bash
# Regenerate host keys if missing
ssh-keygen -A 2>/dev/null || true
# Start SSH daemon in foreground
exec /usr/sbin/sshd -D
EOF
chmod +x /start.sh

echo '[*] db_server setup complete'
echo '[*] admin user created with password: adminpass123'
echo '[*] SSH password authentication enabled on port 22'
echo '[*] Flag planted at /home/admin/flag.txt'
