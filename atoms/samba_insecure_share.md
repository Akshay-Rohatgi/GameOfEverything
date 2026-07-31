---
id: samba_insecure_share
description: Configures an insecure Samba share that is world-readable and writable.
required_vars: [share_name, path]
---
# Atom: Insecure Samba Share
Configures a world-readable/writable share to leak sensitive information.

### Logic Requirements:
1. Create the target directory: `mkdir -p <path>`
2. Set directory permissions: `chmod 777 <path>`
3. Append a configuration block to `/etc/samba/smb.conf`.
4. Reload Samba config — **never `systemctl`** (not available in Docker): `smbcontrol smbd reload-config`

### Common Patterns (Anonymous Access):
```ini
[<share_name>]
   path = <path>
   browseable = yes
   guest ok = yes
   read only = no
   create mask = 0755
```

### Testing Guidance:
1. Verify the directory exists and has the correct permissions: `ls -ld <path>`
2. Check the Samba configuration for the new share: `testparm -s | grep -i <share_name>`
3. From the attacker, verify anonymous access: `smbclient //<target>/share_name -N -c 'ls'`
   - **smbclient sends progress output (`getting file`, `putting file`) to STDERR, not stdout**
   - When downloading, assert on `exit_code: 0` and verify the file on disk (`ls /tmp/file`) — do NOT assert `stdout_regex: 'getting file'`
   - For listing (`ls`), file names appear in stdout; assert with `stdout_regex`

### Synthesis Guidance:
Generate the commands to create the directory, set permissions, append the Samba configuration, and reload with `smbcontrol smbd reload-config`. Never use `systemctl` — it is not available in Docker containers.