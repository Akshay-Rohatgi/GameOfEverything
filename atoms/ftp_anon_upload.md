---
id: ftp_anon_upload
description: Configures vsftpd (or similar FTP daemon) to allow anonymous login with upload and directory creation permissions, creating an unauthenticated file upload vector.
required_vars: [upload_dir]
requires_service: ftp
---
# Atom: FTP Anonymous Upload
An FTP server is configured to accept anonymous logins and permit file uploads. Attackers can upload arbitrary files (e.g. web shells, scripts) without credentials. Combined with a web server serving the same directory, this becomes a direct remote code execution vector.

> **vsftpd is provided by the platform.** The `ftp` service recipe (declared via `requires_service`) installs vsftpd for the target distro, writes `/etc/vsftpd.conf` with anonymous login and upload enabled (`anonymous_enable`, `write_enable`, `anon_upload_enable`, `anon_mkdir_write_enable`), creates a world-writable anon root at `/var/ftp/pub`, and starts the daemon. The entity script MUST NOT install, reconfigure (do not rewrite `/etc/vsftpd.conf`), or start vsftpd — doing so previously caused a double-install. It only prepares the upload target and any web-shell pivot.

### Logic Requirements:
1. The platform's `ftp` recipe has already enabled anonymous upload against `/var/ftp/pub`. Do not install vsftpd, rewrite `/etc/vsftpd.conf`, or start the daemon.
2. If the scenario needs uploaded files to be served/executed (web-shell pivot), make the anonymously-writable directory the one the web server serves — e.g. point the web document root at the anon upload directory, or place `<upload_dir>` where both vsftpd (anon write) and the web server (read/execute) reach it.

### Common Patterns:
- **Web-Shell Vector (combined with a web server):**
  Point the web server's document root at the FTP anon upload directory so a file uploaded over anonymous FTP is reachable (and, for PHP, executed) over HTTP.

### Testing Guidance:
1. Verify vsftpd is running: `pgrep vsftpd` or `ss -tlnp | grep 21`
2. Upload anonymously: `curl -T /etc/hostname ftp://127.0.0.1/test.txt --user anonymous:` — should succeed.
3. Verify the file landed in the anon root: `ls -l /var/ftp/pub/`
4. (Web pivot) Fetch the uploaded file over HTTP from the web-served directory to confirm the RCE vector.

### Synthesis Guidance:
vsftpd install/config/start is owned by the `ftp` recipe — the entity must not duplicate it. The atom's job is the *vector*: ensure anonymously-uploaded files land somewhere exploitable (e.g. a web-served directory). KNOWN LIMITATION: the recipe's anon root is fixed at `/var/ftp/pub`; a scenario needing a custom `upload_dir` (e.g. shared with a PHP web root) requires the `ftp` recipe to expose an `anon_root` config_var — a distro-independent recipe enhancement, deferred.
