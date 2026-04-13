# Sample Requests:

Sample Request 1:
```
Spin up an Ubuntu 22.04 server. It needs an anonymous Samba share containing a backup zip file with a user's username and password. Once I get SSH access, I want to be able to escalate privileges to root by exploiting a misconfigured SUID binary that allows the user to execute /bin/bash with root privileges.
```

In this case the request should be split up accordingly:
```
Context: Spin up an Ubuntu 22.04 server.
Initial Access Vector(s): It needs an anonymous Samba share containing a backup zip file with a user's username and password.
Privilege Escalation Vector(s): Once I get SSH access, I want to be able to escalate privileges to root by exploiting a misconfigured SUID binary that allows the user to execute /bin/bash with root privileges.
```

 1. Flask + SSTI → RCE (different runtime, no DB)
  ▎ "I want a Python Flask internal wiki where the page rendering is vulnerable to server-side template injection, and once I have RCE I can escalate to root via a passwordless sudo rule"

  Tests: ssti_jinja2 + rce_via_cmd_injection + flask runtime + sudoers_no_passwd misconfig atom

  ---
  2. File upload → webshell (PHP, no SQLi)
  ▎ "I want a web server with a file upload form that doesn't validate file types, so I can upload a PHP webshell and get remote code execution"

  Tests: file_upload_bypass + rce_via_webshell + apache_php runtime — no bridged OS credentials, simpler flow

  ---
  3. Misconfig-only (no custom app) — regression test
  ▎ "I want a server with an anonymous-access Samba share containing an SSH private key, and once I use the key to log in I can escalate via a SUID vim binary"

  Tests: that the existing misconfig pipeline still works correctly after all the Phase 4 changes — no custom_vectors should be produced

  ---
  4. Mixed — custom app + misconfig on same box
  ▎ "I want a PHP login page vulnerable to SQL injection that leaks the password for a local user called mgarcia, and that same user has a cron job running a world-writable script I can
  modify to get root"


```
Create two public facing Ubuntu servers. One hosts a single-file PHP based file manager that is vulnerable to arbitrary uploads. The base64 binary on this system has the suid bit, and can be used to read a sensitive file found in the root users home directory. This file contains low-privilege, human user credentials to the other server. The other server is a backup server with a cronjob that runs a world writable script as root to audit current backups on the system. The attacker should gain unauthenticated rce on the first server via arbitrary upload and read the sensitive file using the suid binary. Then, the attacker will ssh into the other machine, and abuse the vulnerable cronjob script to escalate to root.
```