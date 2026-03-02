---
id: mongodb_disable_auth
description: Configures MongoDB to run without authentication and to bind to all network interfaces, exposing all databases to unauthenticated remote access.
required_vars: []
---
# Atom: MongoDB Disable Auth
MongoDB has historically shipped with authentication disabled by default. This atom ensures `authorization` is disabled in `mongod.conf` and that MongoDB listens on all interfaces, allowing any host to connect and read or write any database.

### Logic Requirements:
1. Install MongoDB: `apt-get install -y mongodb` (or `mongodb-org` for the official package)
2. Configure `mongod.conf` to bind to all interfaces and disable auth:
   - Set `net.bindIp: 0.0.0.0`
   - Set `security.authorization: disabled` (or remove the security section entirely)
3. Start MongoDB: `mongod --config /etc/mongod.conf --fork --logpath /var/log/mongod.log` (or `service mongod start`)

### Common Patterns:
- **Edit mongod.conf:**
  ```yaml
  # /etc/mongod.conf
  net:
    port: 27017
    bindIp: 0.0.0.0
  security:
    authorization: disabled
  ```
- **Inline Flag (Docker-friendly, no config file):**
  ```bash
  mongod --bind_ip_all --noauth --fork --logpath /var/log/mongod.log
  ```
- **Minimal sed replacement if config file exists:**
  ```bash
  sed -i 's/bindIp: 127.0.0.1/bindIp: 0.0.0.0/' /etc/mongod.conf
  sed -i 's/authorization: enabled/authorization: disabled/' /etc/mongod.conf
  ```

### Testing Guidance:
1. Verify MongoDB is listening on 0.0.0.0:27017: `ss -tlnp | grep 27017`
2. Connect without credentials: `mongosh --host 127.0.0.1 --eval "db.adminCommand('listDatabases')"` — should return a database list.
3. From outside the container: `mongosh --host <container_ip> --eval "db.adminCommand('listDatabases')"`
4. Verify no authentication is required: `mongosh --host 127.0.0.1 admin --eval "db.getUsers()"` — should succeed without credentials.

### Synthesis Guidance:
In Docker, prefer the inline `mongod` invocation with `--bind_ip_all --noauth` to avoid distribution-specific config file locations. Ensure MongoDB starts at container launch. Combine with `sensitive_file` or `exposed_env_vars` to plant credentials inside MongoDB collections for attackers to discover. Note: `mongosh` is the modern Mongo shell; use `mongo` for older versions.
