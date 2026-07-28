---
id: exposed_env_vars
description: Appends sensitive environment variable exports (credentials, secrets, tokens) to a system-wide environment file (/etc/environment or /etc/profile), making them visible to all users who log in.
required_vars: [var_name, var_value]
---
# Atom: Exposed Environment Variables
Credentials and secrets are hardcoded as environment variables in a system-wide login file. Any user who opens a shell will inherit these variables, and they are trivially discoverable via `printenv` or `cat /etc/environment`.

### Logic Requirements:
1. Append the variable export to `/etc/environment` (simple `KEY=VALUE` format, no export keyword) or to `/etc/profile` (uses `export KEY=VALUE`):
   - For `/etc/environment`: `echo '<var_name>=<var_value>' >> /etc/environment`
   - For `/etc/profile`: `echo 'export <var_name>=<var_value>' >> /etc/profile`
2. Ensure the file remains world-readable (default on most Linux systems).

### Common Patterns:
- **Database Credentials in /etc/environment:**
  ```bash
  echo "DB_PASSWORD=Sup3rS3cret!" >> /etc/environment
  echo "DB_USER=root" >> /etc/environment
  echo "DB_HOST=localhost" >> /etc/environment
  ```
- **API Key in /etc/profile:**
  ```bash
  echo "export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE" >> /etc/profile
  echo "export AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" >> /etc/profile
  ```
- **Admin Password:**
  ```bash
  echo "ADMIN_PASSWORD=changeme123" >> /etc/environment
  ```
- **Internal API Token:**
  ```bash
  echo "export INTERNAL_API_TOKEN=tok_live_abc123def456" >> /etc/profile
  ```

### Testing Guidance:
1. Verify the variable is present in the file: `grep <var_name> /etc/environment /etc/profile`
2. Source the file manually if needed: `source /etc/environment` or `source /etc/profile`
3. Verify the variable is exported into the shell environment: `printenv <var_name>` — should return `<var_value>`
4. Confirm world-readability: `ls -l /etc/environment` and `ls -l /etc/profile`

### Synthesis Guidance:
Use secret values that are reused elsewhere in the scenario (e.g. the same `DB_PASSWORD` that another atom uses to configure a database), so the attacker can discover the credential here and use it to pivot. Prefer `/etc/environment` for simple key=value credentials and `/etc/profile` for complex shell expressions. `/etc/environment` does not support shell syntax, so it cannot have spaces around `=` or use `export`.
