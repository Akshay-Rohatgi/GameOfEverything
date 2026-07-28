---
id: redis_disable_auth
description: Configures Redis to require no authentication by removing or commenting out the requirepass directive and binding to all interfaces, exposing it to unauthenticated remote access.
required_vars: []
---
# Atom: Redis Disable Auth
Redis ships with authentication disabled by default in many distributions. This atom explicitly ensures no password is required and that Redis is reachable on all network interfaces, making it accessible to any host that can reach the port.

### Logic Requirements:
1. Install Redis: `apt-get install -y redis-server`
2. Configure Redis to bind to all interfaces: set `bind 0.0.0.0` in `/etc/redis/redis.conf`
3. Remove or comment out any `requirepass` directive: `sed -i 's/^requirepass/#requirepass/' /etc/redis/redis.conf`
4. Disable protected mode: `sed -i 's/^protected-mode yes/protected-mode no/' /etc/redis/redis.conf`
5. Start Redis: `service redis-server start` (or `redis-server /etc/redis/redis.conf --daemonize yes` in Docker)

### Common Patterns:
- **Full No-Auth Redis Config Block:**
  ```bash
  sed -i 's/^bind 127.0.0.1/bind 0.0.0.0/' /etc/redis/redis.conf
  sed -i 's/^protected-mode yes/protected-mode no/' /etc/redis/redis.conf
  sed -i '/^requirepass/d' /etc/redis/redis.conf
  ```
- **Docker Inline Config (no config file):**
  ```bash
  redis-server --bind 0.0.0.0 --protected-mode no --daemonize yes
  ```

### Testing Guidance:
1. Verify Redis is listening on 0.0.0.0: `ss -tlnp | grep 6379`
2. Connect without credentials: `redis-cli -h 127.0.0.1 ping` — should return `PONG`
3. Dump all keys: `redis-cli -h 127.0.0.1 keys '*'`
4. From outside the container (attacker context): `redis-cli -h <container_ip> ping` — should return `PONG`

### Synthesis Guidance:
In Docker, prefer the inline `redis-server` invocation to avoid config file path issues. Ensure the Redis process is started at container launch (add to a startup script or use Docker CMD). This atom is a prerequisite for `redis_replication_leak`.
