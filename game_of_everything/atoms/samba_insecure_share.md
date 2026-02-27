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
4. Restart the service: `systemctl restart smbd`

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
2. Check the Samba configuration for the new share: `testparm -s | grep <share_name>`

### Synthesis Guidance:
Generate the commands to create the directory, set permissions, append the Samba configuration, and restart the Samba service. 