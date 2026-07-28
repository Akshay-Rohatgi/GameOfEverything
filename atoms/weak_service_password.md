---
id: weak_service_password
description: Configures a service to use a weak, default, or easily guessable password for its primary user account, enabling credential-based access with minimal effort.
required_vars: [service, username, password]
---
# Atom: Weak Service Password
A service is configured with a weak or well-known password for its admin or default user account. This enables password-spray, brute-force, or default-credential attacks against the service.

### Logic Requirements:
1. Install the target service if not already present.
2. Set (or reset) the service account's password to `<password>`.
3. Ensure the account has the appropriate access level (admin, root, superuser).

### Common Patterns:

**MySQL / MariaDB (root user):**
```bash
service mysql start
mysql -e "ALTER USER 'root'@'localhost' IDENTIFIED BY '<password>'; FLUSH PRIVILEGES;"
mysql -e "CREATE USER 'root'@'%' IDENTIFIED BY '<password>'; GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION; FLUSH PRIVILEGES;"
```

**PostgreSQL (postgres superuser):**
```bash
service postgresql start
su -c "psql -c \"ALTER USER postgres PASSWORD '<password>';\"" postgres
```

**Redis (requirepass):**
```bash
echo "requirepass <password>" >> /etc/redis/redis.conf
# Or inline: redis-server --requirepass <password> --daemonize yes
```

**SSH / Linux system user (PAM):**
```bash
useradd -m -s /bin/bash <username>
echo "<username>:<password>" | chpasswd
```

**FTP (local user):**
```bash
useradd -m -s /bin/bash <username>
echo "<username>:<password>" | chpasswd
```

**MongoDB:**
```bash
mongosh admin --eval "db.createUser({user: '<username>', pwd: '<password>', roles: [{role: 'root', db: 'admin'}]})"
```

**HTTP Basic Auth (Apache .htpasswd):**
```bash
htpasswd -cb /etc/apache2/.htpasswd <username> <password>
```

### Testing Guidance:
1. Attempt to authenticate with the configured credentials:
   - MySQL: `mysql -u <username> -p<password> -e 'select 1'`
   - PostgreSQL: `psql -U postgres -c 'select 1'` (with `PGPASSWORD=<password>`)
   - Redis: `redis-cli -a <password> ping`
   - SSH: `sshpass -p '<password>' ssh <username>@localhost 'id'`
2. Verify that authentication succeeds and grants the expected access level.

### Synthesis Guidance:
Choose a `password` appropriate to the scenario's realism: use words from `rockyou.txt` (obtain via `get_rockyou_password()` tool), common defaults (`admin`, `password`, `<service_name>123`), or the service's documented default credentials. The same password should ideally be reused across atoms in the scenario (e.g. planted in `bash_history_leak` or `sensitive_file`) to create a realistic credential chain. The Builder should generate the correct command for the specified `service`.
