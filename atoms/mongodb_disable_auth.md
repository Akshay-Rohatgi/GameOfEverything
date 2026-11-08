---
id: mongodb_disable_auth
description: Configures MongoDB to run without authentication and to bind to all network interfaces, exposing all databases to unauthenticated remote access.
required_vars: []
requires_service: mongodb
---
# Atom: MongoDB Disable Auth
MongoDB has historically shipped with authentication disabled by default. This atom ensures `authorization` is disabled in `mongod.conf` and that MongoDB listens on all interfaces, allowing any host to connect and read or write any database.

> **MongoDB is provided by the platform.** The `mongodb` service recipe (declared via
> `requires_service`) installs the correct server package for the target distro and
> starts `mongod` without systemd. The entity script MUST NOT install MongoDB or add a
> package repository. It applies only the vulnerability: binding to all interfaces and
> disabling auth, then bouncing `mongod` so the new bind address takes effect (a `bindIp`
> change requires a restart — there is no live reload for it).

### Logic Requirements:
1. Edit `/etc/mongod.conf` so MongoDB binds to all interfaces and auth is off:
   - Set `net.bindIp: 0.0.0.0`
   - Set `security.authorization: disabled` (or remove the `security` section entirely)
2. Restart `mongod` so the new `bindIp` takes effect — WITHOUT a distro init system (no `systemctl`): stop the running daemon and relaunch it detached as the `mongodb` user.

### Common Patterns:
- **Open the bind address and disable auth in the config the recipe installed:**
  ```bash
  sed -i 's/^\( *bindIp:\).*/\1 0.0.0.0/' /etc/mongod.conf
  # Ensure auth is not enabled (remove any security.authorization: enabled)
  sed -i 's/^\( *authorization:\).*/\1 disabled/' /etc/mongod.conf
  ```
- **Bounce mongod without systemd (the recipe's start model):**
  ```bash
  pkill mongod 2>/dev/null; sleep 2
  su mongodb -s /bin/bash -c "mongod --config /etc/mongod.conf --fork" \
    || mongod --config /etc/mongod.conf --fork
  ```

### Testing Guidance:
1. Verify MongoDB is listening on 0.0.0.0:27017: `ss -tlnp | grep 27017`
2. Connect without credentials: `mongosh --host 127.0.0.1 --eval "db.adminCommand('listDatabases')"` — should return a database list.
3. From outside the container: `mongosh --host <container_ip> --eval "db.adminCommand('listDatabases')"`
4. Verify no authentication is required: `mongosh --host 127.0.0.1 admin --eval "db.getUsers()"` — should succeed without credentials.

### Synthesis Guidance:
Install/start of `mongod` is owned by the `mongodb` recipe; the entity must not duplicate it or add the MongoDB apt repo. The atom edits the recipe-installed `/etc/mongod.conf` (bind all + auth off) and restarts the daemon. Combine with `sensitive_file` or `exposed_env_vars` to plant credentials inside MongoDB collections for attackers to discover. Note: `mongosh` is the modern Mongo shell; use `mongo` for older versions.
