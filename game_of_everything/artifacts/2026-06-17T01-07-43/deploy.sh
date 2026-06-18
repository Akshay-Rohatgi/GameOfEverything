#!/bin/bash
set -e
# --- sqli_ssh_credential_leak ---
export DEBIAN_FRONTEND=noninteractive

# Install runtime
apt-get update -qq
apt-get install -y build-essential python3 ca-certificates curl gnupg
mkdir -p /etc/apt/keyrings
curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list
apt-get update && apt-get install -y nodejs

mkdir -p /opt/webapp
# Write app.js
echo 'Y29uc3QgZXhwcmVzcyA9IHJlcXVpcmUoJ2V4cHJlc3MnKTsKY29uc3QgRGF0YWJhc2UgPSByZXF1aXJlKCdiZXR0ZXItc3FsaXRlMycpOwoKY29uc3QgYXBwID0gZXhwcmVzcygpOwpjb25zdCBQT1JUID0gMzAwMDsKCi8vIEluaXRpYWxpemUgU1FMaXRlIGRhdGFiYXNlCmNvbnN0IGRiID0gbmV3IERhdGFiYXNlKCcvb3B0L3dlYmFwcC9jYXRhbG9nLmRiJyk7CgovLyBDcmVhdGUgdGFibGVzIGFuZCBzZWVkIGRhdGEKZGIuZXhlYyhgCiAgQ1JFQVRFIFRBQkxFIElGIE5PVCBFWElTVFMgcHJvZHVjdHMgKAogICAgaWQgSU5URUdFUiBQUklNQVJZIEtFWSBBVVRPSU5DUkVNRU5ULAogICAgbmFtZSBURVhULAogICAgZGVzY3JpcHRpb24gVEVYVAogICk7CiAgQ1JFQVRFIFRBQkxFIElGIE5PVCBFWElTVFMgdXNlcnMgKAogICAgaWQgSU5URUdFUiBQUklNQVJZIEtFWSBBVVRPSU5DUkVNRU5ULAogICAgdXNlcm5hbWUgVEVYVCwKICAgIHBhc3N3b3JkIFRFWFQsCiAgICByb2xlIFRFWFQKICApOwpgKTsKCmNvbnN0IHByb2R1Y3RDb3VudCA9IGRiLnByZXBhcmUoJ1NFTEVDVCBDT1VOVCgqKSBhcyBjbnQgRlJPTSBwcm9kdWN0cycpLmdldCgpOwppZiAocHJvZHVjdENvdW50LmNudCA9PT0gMCkgewogIGRiLnByZXBhcmUoIklOU0VSVCBJTlRPIHByb2R1Y3RzIChuYW1lLCBkZXNjcmlwdGlvbikgVkFMVUVTICg/LCA/KSIpLnJ1bignV2lkZ2V0IEEnLCAnQSBiYXNpYyB3aWRnZXQgZm9yIGdlbmVyYWwgdXNlJyk7CiAgZGIucHJlcGFyZSgiSU5TRVJUIElOVE8gcHJvZHVjdHMgKG5hbWUsIGRlc2NyaXB0aW9uKSBWQUxVRVMgKD8sID8pIikucnVuKCdXaWRnZXQgQicsICdBbiBhZHZhbmNlZCB3aWRnZXQgd2l0aCBleHRyYSBmZWF0dXJlcycpOwogIGRiLnByZXBhcmUoIklOU0VSVCBJTlRPIHByb2R1Y3RzIChuYW1lLCBkZXNjcmlwdGlvbikgVkFMVUVTICg/LCA/KSIpLnJ1bignR2FkZ2V0IFgnLCAnQSBjb21wYWN0IGdhZGdldCBmb3IgZXZlcnlkYXkgdGFza3MnKTsKICBkYi5wcmVwYXJlKCJJTlNFUlQgSU5UTyBwcm9kdWN0cyAobmFtZSwgZGVzY3JpcHRpb24pIFZBTFVFUyAoPywgPykiKS5ydW4oJ0dhZGdldCBZJywgJ0EgcHJlbWl1bSBnYWRnZXQgd2l0aCBleHRlbmRlZCB3YXJyYW50eScpOwp9Cgpjb25zdCB1c2VyQ291bnQgPSBkYi5wcmVwYXJlKCdTRUxFQ1QgQ09VTlQoKikgYXMgY250IEZST00gdXNlcnMnKS5nZXQoKTsKaWYgKHVzZXJDb3VudC5jbnQgPT09IDApIHsKICBkYi5wcmVwYXJlKCJJTlNFUlQgSU5UTyB1c2VycyAodXNlcm5hbWUsIHBhc3N3b3JkLCByb2xlKSBWQUxVRVMgKD8sID8sID8pIikucnVuKCdzeXNhZG1pbicsICdTdXAzclMzY3VyM1NTSCEnLCAnYWRtaW4nKTsKICBkYi5wcmVwYXJlKCJJTlNFUlQgSU5UTyB1c2VycyAodXNlcm5hbWUsIHBhc3N3b3JkLCByb2xlKSBWQUxVRVMgKD8sID8sID8pIikucnVuKCdkZXBsb3knLCAnZGVwbG95X3Bhc3NfMjAyNCcsICdzZXJ2aWNlJyk7Cn0KCmNvbnNvbGUubG9nKCdEYXRhYmFzZSBpbml0aWFsaXplZCcpOwoKYXBwLmdldCgnLycsIChyZXEsIHJlcykgPT4gewogIHJlcy5zZW5kKGA8IURPQ1RZUEUgaHRtbD4KPGh0bWw+CjxoZWFkPjx0aXRsZT5Qcm9kdWN0IENhdGFsb2c8L3RpdGxlPjwvaGVhZD4KPGJvZHk+CjxoMT5Qcm9kdWN0IENhdGFsb2c8L2gxPgo8cD5XZWxjb21lIHRvIG91ciBwcm9kdWN0IGNhdGFsb2cuIFVzZSB0aGUgc2VhcmNoIGZvcm0gYmVsb3cgdG8gZmluZCBwcm9kdWN0cy48L3A+Cjxmb3JtIGFjdGlvbj0iL3NlYXJjaCIgbWV0aG9kPSJHRVQiPgogIDxpbnB1dCB0eXBlPSJ0ZXh0IiBuYW1lPSJxIiBwbGFjZWhvbGRlcj0iU2VhcmNoIHByb2R1Y3RzLi4uIiAvPgogIDxidXR0b24gdHlwZT0ic3VibWl0Ij5TZWFyY2g8L2J1dHRvbj4KPC9mb3JtPgo8L2JvZHk+CjwvaHRtbD5gKTsKfSk7CgphcHAuZ2V0KCcvc2VhcmNoJywgKHJlcSwgcmVzKSA9PiB7CiAgY29uc3QgcSA9IHJlcS5xdWVyeS5xIHx8ICcnOwogIGNvbnN0IHNxbCA9ICJTRUxFQ1QgbmFtZSwgZGVzY3JpcHRpb24gRlJPTSBwcm9kdWN0cyBXSEVSRSBuYW1lIExJS0UgJyUiICsgcSArICIlJyI7CiAgdHJ5IHsKICAgIGNvbnN0IHJlc3VsdHMgPSBkYi5wcmVwYXJlKHNxbCkuYWxsKCk7CiAgICByZXMuanNvbihyZXN1bHRzKTsKICB9IGNhdGNoIChlcnIpIHsKICAgIHJlcy5zdGF0dXMoNTAwKS5qc29uKHsgZXJyb3I6IGVyci5tZXNzYWdlIH0pOwogIH0KfSk7CgphcHAuZ2V0KCcvaGVhbHRoJywgKHJlcSwgcmVzKSA9PiB7CiAgcmVzLmpzb24oeyBzdGF0dXM6ICdvaycgfSk7Cn0pOwoKYXBwLmxpc3RlbihQT1JULCAnMC4wLjAuMCcsICgpID0+IHsKICBjb25zb2xlLmxvZyhgU2VydmVyIHJ1bm5pbmcgb24gMC4wLjAuMDoke1BPUlR9YCk7Cn0pOwo=' | base64 -d > /opt/webapp/app.js

cd /opt/webapp
cd /opt/webapp && if [ -f package.json ]; then npm install better-sqlite3; else npm init -y && npm install express better-sqlite3; fi

# Start application
cd /opt/webapp
nohup node app.js > /var/log/webapp.log 2>&1 &
sleep 3

# Healthcheck
curl -sf http://localhost:3000/ || curl -sf http://localhost:3000/health && echo 'deploy_ok' || echo 'deploy_failed'

# --- ssh_login_leaked_creds ---

# Install required packages
apt-get update -y
apt-get install -y openssh-server sshpass

# Create low-privilege user sysadmin if not exists
if ! id -u sysadmin >/dev/null 2>&1; then
    useradd -m -s /bin/bash sysadmin
fi
echo 'sysadmin:Sup3rS3cur3SSH!' | chpasswd

# Ensure sysadmin is NOT in sudo group
gpasswd -d sysadmin sudo 2>/dev/null || true

# Place a flag file in the home directory
mkdir -p /home/sysadmin
echo 'FLAG{ssh_login_via_sqli_credential_leak}' > /home/sysadmin/flag.txt
chown sysadmin:sysadmin /home/sysadmin/flag.txt
chmod 644 /home/sysadmin/flag.txt

# Configure sshd
mkdir -p /var/run/sshd

# Write a clean sshd_config with password authentication enabled
cat > /etc/ssh/sshd_config <<'EOF'
Port 22
ListenAddress 0.0.0.0
Protocol 2
HostKey /etc/ssh/ssh_host_rsa_key
HostKey /etc/ssh/ssh_host_ecdsa_key
HostKey /etc/ssh/ssh_host_ed25519_key
SyslogFacility AUTH
LogLevel INFO
LoginGraceTime 2m
PermitRootLogin no
StrictModes yes
MaxAuthTries 6
PubkeyAuthentication yes
PasswordAuthentication yes
ChallengeResponseAuthentication no
UsePAM yes
X11Forwarding yes
PrintMotd no
AcceptEnv LANG LC_*
Subsystem sftp /usr/lib/openssh/sftp-server
EOF

# Generate SSH host keys if not present
ssh-keygen -A

# Start SSH service (handle both systemd and non-systemd environments)
service ssh start || service sshd start || /usr/sbin/sshd

echo 'SSH setup complete. sysadmin user created with password authentication enabled.'

# --- sudo_privesc_root ---

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
