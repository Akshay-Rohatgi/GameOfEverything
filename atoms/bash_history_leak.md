---
id: bash_history_leak
description: Writes sensitive information (credentials, API keys, tokens) directly into a user's .bash_history file, and ensures the file is readable by other users on the system.
required_vars: [username, sensitive_content]
---
# Atom: Bash History Leak
A user's shell history contains sensitive data entered on the command line—passwords passed as arguments, API tokens in curl commands, database connection strings, etc. Weak file permissions expose this to other local users.

### Logic Requirements:
1. Append `<sensitive_content>` to `/home/<username>/.bash_history`: `echo "<sensitive_content>" >> /home/<username>/.bash_history`
2. Set weak permissions so the file is readable by other users: `chmod 644 /home/<username>/.bash_history`
3. Ensure the file is owned by the target user: `chown <username>:<username> /home/<username>/.bash_history`

### Common Patterns:
- **Password Passed as CLI Argument (MySQL):**
  ```bash
  echo "mysql -u root -psecretpassword123 -e 'show databases;'" >> /home/operator/.bash_history
  chmod 644 /home/operator/.bash_history
  chown operator:operator /home/operator/.bash_history
  ```
- **Hardcoded API Key in curl:**
  ```bash
  echo "curl -H 'Authorization: Bearer ghp_abc123TOKEN' https://api.github.com/user" >> /home/developer/.bash_history
  chmod 644 /home/developer/.bash_history
  ```
- **SSH Private Key Passphrase:**
  ```bash
  echo "openssl rsa -in /root/.ssh/id_rsa -passin pass:MyK3ypass!" >> /home/admin/.bash_history
  chmod 644 /home/admin/.bash_history
  ```
- **SCP with Inline Password (using sshpass):**
  ```bash
  echo "sshpass -p 'P@ssw0rd!' scp -r /var/backups admin@10.0.0.5:/backup" >> /home/admin/.bash_history
  chmod 644 /home/admin/.bash_history
  ```

### Testing Guidance:
1. Verify the history file contains the sensitive content: `cat /home/<username>/.bash_history | grep -i <keyword>`
2. Verify the permissions: `ls -l /home/<username>/.bash_history` — should be world-readable or at least group-readable.
3. As a different low-priv user, attempt to read the file: `cat /home/<username>/.bash_history`

### Synthesis Guidance:
Embed realistic-looking sensitive data matching the scenario—use the same password/token that other atoms (e.g. `weak_service_password`, `sensitive_file`) use so that the credential found in history actually works somewhere on the machine. Combine with `create_user` to ensure the user and home directory exist before writing the history file. The content should look like real commands a developer or sysadmin would have typed.
