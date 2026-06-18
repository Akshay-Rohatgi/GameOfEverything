#!/bin/bash
set -e

# Update package list
apt-get update -y

# Install vim and sudo
apt-get install -y vim sudo

# Create low-privilege user (ignore error if already exists)
useradd -m -s /bin/bash lowpriv 2>/dev/null || true
echo 'lowpriv:lowpriv123' | chpasswd

# Write sudoers rule granting lowpriv NOPASSWD access to vim
echo 'lowpriv ALL=(ALL) NOPASSWD: /usr/bin/vim' > /etc/sudoers.d/lowpriv

# Set correct permissions on sudoers file
chmod 0440 /etc/sudoers.d/lowpriv

# Verify sudoers syntax
visudo -c -f /etc/sudoers.d/lowpriv

echo 'Setup complete. lowpriv user can escalate via: sudo vim -c ":!/bin/bash" /dev/null'
