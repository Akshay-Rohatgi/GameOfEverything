#!/bin/bash
set -e
# --- install_package ---
# Install redis-server non-interactively (required for redis_disable_auth and redis_replication_leak)
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y redis-server

# --- install_package ---
# Install openssh-server non-interactively (required for weak_service_password SSH atom)
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y openssh-server

# --- redis_disable_auth ---
# Configure Redis with no authentication, bound to all interfaces, and protected-mode off
# This exposes Redis to unauthenticated remote access from any host that can reach port 6379

# Bind Redis to all network interfaces (not just loopback)
sed -i 's/^bind 127.0.0.1.*/bind 0.0.0.0/' /etc/redis/redis.conf

# Disable protected mode so external unauthenticated connections are accepted
sed -i 's/^protected-mode yes/protected-mode no/' /etc/redis/redis.conf

# Remove any requirepass directive to ensure no password is enforced
sed -i '/^requirepass/d' /etc/redis/redis.conf

# Start Redis using inline flags for Docker compatibility (daemonized)
redis-server --bind 0.0.0.0 --protected-mode no --daemonize yes

# --- redis_replication_leak ---
# Pre-populate Redis with sensitive credentials that the attacker can retrieve
# The system_password key contains the SSH password for unprivileged_user,
# enabling a realistic pivot: Redis enumeration -> credential retrieval -> SSH shell

# Wait briefly to ensure Redis is fully started before setting keys
sleep 1

# Store a realistic-looking API key (lateral movement / exfiltration scenario)
redis-cli SET api_key "sk-live-f3a92cd1b7e045f8a3c2d9104eb762aa"

# Store the system password that matches the unprivileged_user SSH account
# This value is intentionally retrievable by any unauthenticated Redis client
redis-cli SET system_password "Sup3rS3cr3t!"

# Store an internal service credential for added realism
redis-cli SET db_password "Sup3rS3cr3t!"

# --- weak_service_password ---
# Create an unprivileged system user whose password matches the value stored
# in the Redis 'system_password' key, enabling credential reuse after Redis enumeration

# Install sshpass for SSH password authentication testing
dpkg -l sshpass 2>/dev/null | grep -q '^ii' || apt-get install -y sshpass

# Create the user with a home directory and bash shell (idempotent)
id unprivileged_user 2>/dev/null || useradd -m -s /bin/bash unprivileged_user

# Set the password to match the value stored in Redis (Sup3rS3cr3t!)
# An attacker who retrieves the Redis key can immediately SSH in as this user
echo "unprivileged_user:Sup3rS3cr3t!" | chpasswd

# Ensure SSH is running and password authentication is enabled
mkdir -p /var/run/sshd
sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config

# Start/restart the SSH service
service ssh restart || /usr/sbin/sshd

# --- cron_job_hijack ---
# Create a root-owned cron job that invokes a cleanup script writable by unprivileged_user
# This allows the low-privilege user to overwrite the script with a malicious payload
# that will execute as root when the cron fires every minute

# Create the benign cleanup script with plausible maintenance content
cat > /opt/cleanup.sh << 'EOF'
# Routine cleanup: remove old temp files
find /tmp -type f -mtime +7 -delete
find /var/log -name '*.gz' -mtime +30 -delete
EOF

# Make the script writable by unprivileged_user (world-writable for simplicity)
# This is the intentional misconfiguration enabling privilege escalation
chmod 777 /opt/cleanup.sh
chown root:root /opt/cleanup.sh

# Install the root cron job that invokes the writable script every minute
echo '* * * * * root /opt/cleanup.sh' > /etc/cron.d/maintenance
chmod 644 /etc/cron.d/maintenance

# Ensure cron daemon is running
service cron start || true