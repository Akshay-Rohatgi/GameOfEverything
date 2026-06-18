#!/bin/bash
set -e

# Ensure 'appuser' exists (already created by SSH entity, but create if not present)
if ! id -u appuser &>/dev/null; then
    useradd -m -s /bin/bash appuser
fi

# Ensure sudoers.d directory exists
mkdir -p /etc/sudoers.d

# Write the sudoers drop-in file granting appuser NOPASSWD access to /bin/bash
cat > /etc/sudoers.d/appuser << 'EOF'
appuser ALL=(root) NOPASSWD: /bin/bash
EOF

# Set correct permissions on the drop-in file
chmod 0440 /etc/sudoers.d/appuser

# Ensure sudo package is installed
apt-get install -y sudo 2>/dev/null || true

# Validate the sudoers configuration
visudo -c

echo "Sudo misconfiguration setup complete."
echo "User 'appuser' can now run: sudo /bin/bash"
