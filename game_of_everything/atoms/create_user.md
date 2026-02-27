---
id: create_user
description: Creates a system user with specific shell and home directory settings.
required_vars: [username, password (optional)]
---
# Atom: Create System User
Used to provide realism and a pivot point for pentesters.

### Logic Requirements:
1. Create the user with a home directory: `useradd -m -s /bin/bash <username>`
2. (Optional) Set a weak or known password: `echo "<username>:<password>" | chpasswd`. 
3. Ensure the home directory has appropriate (or weak) permissions.

### Common Patterns:
- **Default User:** `useradd -m -s /bin/bash operator`
- **Service Account:** `useradd -r -s /usr/sbin/nologin web_dev`

### Testing Guidance:
1. Verify the user was created: `id <username>`
2. Check the home directory: `ls -ld /home/<username>`
3. (If password set) Attempt to switch user: `printf "<password>\n" | su - <username>`

### Synthesis Guidance:
Generate the user creation command. For example, if the context implies a "sloppy developer," consider adding the user to a non-standard group or leaving a sensitive file in their home directory. If a password is provided, include the password setting command as well. If a weak password is implied, use the get_rockyou_password() tool 

