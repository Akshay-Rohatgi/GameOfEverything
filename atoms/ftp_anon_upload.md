---
id: ftp_anon_upload
description: Configures vsftpd (or similar FTP daemon) to allow anonymous login with upload and directory creation permissions, creating an unauthenticated file upload vector.
required_vars: [upload_dir]
---
# Atom: FTP Anonymous Upload
An FTP server is configured to accept anonymous logins and permit file uploads. Attackers can upload arbitrary files (e.g. web shells, scripts) without credentials. Combined with a web server serving the same directory, this becomes a direct remote code execution vector.

### Logic Requirements:
1. Install vsftpd: `apt-get install -y vsftpd`
2. Create the upload directory: `mkdir -p <upload_dir>`
3. Set appropriate permissions (vsftpd anon upload requires the directory NOT be owned by ftp but be writable by ftp):
   ```bash
   chown root:root <upload_dir>
   chmod 777 <upload_dir>
   ```
4. Write the vsftpd configuration to `/etc/vsftpd.conf`:
   ```ini
   anonymous_enable=YES
   local_enable=NO
   write_enable=YES
   anon_upload_enable=YES
   anon_mkdir_write_enable=YES
   anon_root=<upload_dir>
   dirmessage_enable=YES
   xferlog_enable=YES
   listen=YES
   ```
5. Start vsftpd: `vsftpd /etc/vsftpd.conf &` (or `service vsftpd start`)

### Common Patterns:
- **Basic Anon Upload Config:**
  ```bash
  apt-get install -y vsftpd
  mkdir -p /srv/ftp/uploads
  chown root:ftp /srv/ftp/uploads
  chmod 730 /srv/ftp/uploads
  cat > /etc/vsftpd.conf << 'EOF'
  listen=YES
  anonymous_enable=YES
  write_enable=YES
  anon_upload_enable=YES
  anon_mkdir_write_enable=YES
  anon_root=/srv/ftp
  EOF
  vsftpd /etc/vsftpd.conf &
  ```
- **FTP + Web Shell Vector (combined with web server):**
  Set `anon_root` to the web server's document root or a directory served by the web server.

### Testing Guidance:
1. Verify vsftpd is running: `pgrep vsftpd` or `ss -tlnp | grep 21`
2. Connect anonymously: `ftp -n 127.0.0.1` then `user anonymous ""` — should log in successfully.
3. Test upload: `echo "test" | ftp -n 127.0.0.1 <<< $'user anonymous ""\nput /etc/hostname uploads/test.txt'`
4. Alternatively: `curl -T /etc/hostname ftp://127.0.0.1/<upload_dir>/test.txt --user anonymous:`
5. Verify the file landed: `ls -l <upload_dir>/test.txt`

### Synthesis Guidance:
vsftpd has strict permission requirements for anonymous upload directories — the directory must NOT be owned by the `ftp` user (to prevent it from being a writable root), but a subdirectory (`uploads/`) should be writable. The generated script should create both the anon root (owned by root) and an uploads subdirectory (writable by ftp/other). In Docker, start vsftpd in the foreground or with `&` — not via systemctl unless systemd is available.
