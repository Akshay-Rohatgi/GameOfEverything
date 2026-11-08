---
id: weak_service_password
description: Configures a service to use a weak, default, or easily guessable password for its primary user account, enabling credential-based access with minimal effort.
required_vars: [service, username, password]
requires_service_by:
  var: service
  map:
    mysql: mysql
    mariadb: mysql
    postgres: postgres
    postgresql: postgres
    redis: redis
    ftp: ftp
    mongodb: mongodb
    mongo: mongodb
---
# Atom: Weak Service Password
A service is configured with a weak or well-known password for its admin or default user account. This enables password-spray, brute-force, or default-credential attacks against the service.

> **Service daemons are provided by the platform.** The planner pins which service
> this is via `config.service`, and when that service has a recipe (resolved through
> `requires_service_by`) the recipe installs and starts it. The entity script MUST NOT
> install or start the daemon in that case; it only sets the weak credential. (For a
> plain SSH/Linux system account there is no daemon to install — `sshd` ships in the
> base image — so just create the user and set the password.) Use the `service`,
> `username`, and `password` the planner pinned — do not substitute your own.

### Logic Requirements:
1. Set (or reset) the account's password for the pinned `<service>` to `<password>`.
2. Ensure the account `<username>` has the appropriate access level (admin, root, superuser).
3. Do not reinstall or restart a recipe-provided daemon with a distro init system (no `systemctl`); a reload is enough if the change needs one.

### Common Patterns:

**MySQL / MariaDB (root user) — service already running:**
```bash
# Do ALL of this in ONE `mysql -e` call = ONE connection. On Debian's MariaDB,
# root@localhost uses unix_socket auth (passwordless as system root). If you set a
# password on root@localhost in one `mysql -e` and then run a SECOND `mysql -e`, that
# second connection has no socket auth and is denied — so create/grant the remote user
# FIRST and change root@localhost LAST, in a single semicolon-separated statement.
# (Use `mysql -e`, not a heredoc — heredocs nest badly inside generated setup scripts.)
mysql -e "CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED BY '<password>'; ALTER USER 'root'@'%' IDENTIFIED BY '<password>'; GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION; ALTER USER 'root'@'localhost' IDENTIFIED BY '<password>'; FLUSH PRIVILEGES;"
```

**PostgreSQL (postgres superuser) — service already running:**
```bash
su -c "psql -c \"ALTER USER postgres PASSWORD '<password>';\"" postgres
```

**Redis (requirepass):**
```bash
echo "requirepass <password>" >> /etc/redis/redis.conf
# reload via the running server, not a restart:
redis-cli CONFIG SET requirepass '<password>'
```

**SSH / Linux system user (PAM) — no daemon install (sshd is in the base image):**
```bash
useradd -m -s /bin/bash <username>
echo "<username>:<password>" | chpasswd
```

**FTP (local user) — vsftpd provided by the ftp recipe:**
```bash
useradd -m -s /bin/bash <username>
echo "<username>:<password>" | chpasswd
```

### Testing Guidance:
1. Attempt to authenticate with the configured credentials:
   - MySQL: `mysql -u <username> -p<password> -e 'select 1'`
   - PostgreSQL: `PGPASSWORD=<password> psql -U postgres -c 'select 1'`
   - Redis: `redis-cli -a <password> ping`
   - SSH: `sshpass -p '<password>' ssh <username>@localhost 'id'`
2. Verify that authentication succeeds and grants the expected access level.

### Synthesis Guidance:
Choose a `password` appropriate to the scenario's realism: use words from `rockyou.txt` (obtain via `get_rockyou_password()` tool), common defaults (`admin`, `password`, `<service_name>123`), or the service's documented default credentials. The same password should ideally be reused across atoms in the scenario (e.g. planted in `bash_history_leak` or `sensitive_file`) to create a realistic credential chain — which is exactly why the planner pins it in `config` rather than each atom inventing its own. Install/start of a recipe-backed service is owned by that recipe; the atom only sets the credential.
