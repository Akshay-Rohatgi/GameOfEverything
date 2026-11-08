---
id: redis_disable_auth
description: Configures Redis to require no authentication by removing or commenting out the requirepass directive and binding to all interfaces, exposing it to unauthenticated remote access.
required_vars: []
requires_service: redis
---
# Atom: Redis Disable Auth
Redis ships with authentication disabled by default in many distributions. This atom explicitly ensures no password is required and that Redis is reachable on all network interfaces, making it accessible to any host that can reach the port.

> **Redis is provided by the platform.** The `redis` service recipe (declared via `requires_service`) installs the correct package for the target distro and starts the daemon. The entity script MUST NOT install or start Redis itself — install no package manager commands, and do not daemonize `redis-server`. Apply only the no-auth misconfiguration below against the config the recipe wrote at `/etc/redis/redis.conf`.

### Logic Requirements:
1. Bind Redis to all interfaces: set `bind 0.0.0.0` in `/etc/redis/redis.conf`.
2. Remove or comment out any `requirepass` directive: `sed -i 's/^requirepass/#requirepass/' /etc/redis/redis.conf`.
3. Disable protected mode: `sed -i 's/^protected-mode yes/protected-mode no/' /etc/redis/redis.conf`.
4. Reload Redis so the config takes effect. The platform manages the Redis process — signal it to re-read its config (e.g. `redis-cli shutdown nosave` then let the recipe restart it, or send the running process a reload) rather than invoking a distro init system.

### Common Patterns:
- **Full No-Auth Redis Config Block:**
  ```bash
  sed -i 's/^bind 127.0.0.1/bind 0.0.0.0/' /etc/redis/redis.conf
  sed -i 's/^protected-mode yes/protected-mode no/' /etc/redis/redis.conf
  sed -i '/^requirepass/d' /etc/redis/redis.conf
  ```

### Testing Guidance:
1. Verify Redis is listening on 0.0.0.0: `ss -tlnp | grep 6379`
2. Connect without credentials: `redis-cli -h 127.0.0.1 ping` — should return `PONG`
3. Dump all keys: `redis-cli -h 127.0.0.1 keys '*'`
4. From outside the container (attacker context): `redis-cli -h <container_ip> ping` — should return `PONG`

### Synthesis Guidance:
Redis is installed and started by the platform's `redis` service recipe — the entity must not install or daemonize Redis itself. Apply only the no-auth configuration to `/etc/redis/redis.conf`. This atom is a prerequisite for `redis_replication_leak`.
