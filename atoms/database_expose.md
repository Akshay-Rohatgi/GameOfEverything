---
id: database_expose
description: Configures a database server to listen on all network interfaces (0.0.0.0) instead of localhost only, exposing it to direct remote connections from any host.
required_vars: [db_type]
---
# Atom: Database Network Exposure
A database server is configured to accept connections from any IP address rather than being locked to localhost. Combined with weak or absent authentication, this allows direct remote access to the database.

### Logic Requirements:
1. Install the database server appropriate for `<db_type>`.
2. Modify the bind address configuration to `0.0.0.0` or equivalent.
3. Restart the database service.

### Common Patterns:

**MySQL / MariaDB:**
```bash
# Option A: my.cnf edit
sed -i 's/^bind-address\s*=.*/bind-address = 0.0.0.0/' /etc/mysql/mysql.conf.d/mysqld.cnf
# Option B: add to [mysqld] if not present
echo "bind-address = 0.0.0.0" >> /etc/mysql/conf.d/expose.cnf
# Restart
service mysql start  # or mysqld_safe --bind-address=0.0.0.0 &
# Grant remote access to root
mysql -e "GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' IDENTIFIED BY 'root' WITH GRANT OPTION; FLUSH PRIVILEGES;"
```

**PostgreSQL:**
```bash
# Edit postgresql.conf
sed -i "s/#listen_addresses = 'localhost'/listen_addresses = '*'/" /etc/postgresql/*/main/postgresql.conf
# Edit pg_hba.conf to allow remote connections
echo "host all all 0.0.0.0/0 md5" >> /etc/postgresql/*/main/pg_hba.conf
service postgresql start
```

**MongoDB:**
```bash
# See mongodb_disable_auth atom for full config
sed -i 's/bindIp: 127.0.0.1/bindIp: 0.0.0.0/' /etc/mongod.conf
```

**Redis:**
```bash
# See redis_disable_auth atom for full config
sed -i 's/^bind 127.0.0.1/bind 0.0.0.0/' /etc/redis/redis.conf
```

**MS SQL Server (mssql-server):**
```bash
# MSSQL listens on 0.0.0.0:1433 by default; ensure firewall is not blocking.
/opt/mssql/bin/mssql-conf set network.tcpport 1433
```

### Testing Guidance:
1. Verify the service is listening on `0.0.0.0` or `*`:
   - MySQL: `ss -tlnp | grep 3306` — should show `0.0.0.0:3306`
   - PostgreSQL: `ss -tlnp | grep 5432` — should show `0.0.0.0:5432`
   - MongoDB: `ss -tlnp | grep 27017`
2. From outside the container (attacker context), connect to `<container_ip>:<port>` using the appropriate client.
3. Verify data is accessible: run a query or list databases.

### Synthesis Guidance:
The Builder should generate the correct configuration change for the specified `db_type`. When multiple databases are installed, this atom can be applied to each. Always verify the exact config file path for the installed version (e.g. PostgreSQL paths vary by version: `/etc/postgresql/14/main/` vs `/etc/postgresql/16/main/`). Combine with `weak_service_password` or auth-disabling atoms to make the exposure directly exploitable.
