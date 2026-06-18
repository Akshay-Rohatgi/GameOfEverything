#!/bin/bash
set -e

# Update package lists
apt-get update -y

# Install openssh-server and sudo if not present
apt-get install -y openssh-server sudo

# Create the low-privilege user 'lowpriv' with password 'lowpriv123'
if ! id -u lowpriv &>/dev/null; then
    useradd -m -s /bin/bash lowpriv
fi
echo 'lowpriv:lowpriv123' | chpasswd

# Configure SSH to allow password authentication
mkdir -p /var/run/sshd

# Ensure PasswordAuthentication is set to yes
if grep -q '^PasswordAuthentication' /etc/ssh/sshd_config; then
    sed -i 's/^PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
else
    echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config
fi

# Ensure PermitRootLogin is set
if grep -q '^PermitRootLogin' /etc/ssh/sshd_config; then
    sed -i 's/^PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
else
    echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config
fi

# Comment out any existing conflicting lines
sed -i 's/^#PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config

# Generate SSH host keys if they don't exist
ssh-keygen -A

# Write the misconfigured sudoers rule (NOPASSWD: /bin/bash)
mkdir -p /etc/sudoers.d
echo 'lowpriv ALL=(ALL) NOPASSWD: /bin/bash' > /etc/sudoers.d/lowpriv
chmod 0440 /etc/sudoers.d/lowpriv

# Validate sudoers file syntax
visudo -c -f /etc/sudoers.d/lowpriv

# Place a flag file at /root/flag.txt
echo 'FLAG{sudo_misconfiguration_pwned}' > /root/flag.txt
chmod 600 /root/flag.txt

# Start SSH daemon in the foreground
exec /usr/sbin/sshd -D
