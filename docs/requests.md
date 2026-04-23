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

```
I want a server hosting a Wordpress site with default admin credentials. Once the attacker logs in, they should be able to read an archived post containing user credentials for a local user. The Wordpress site should be running on an Apache server with PHP, and the local user should have a SUID binary that allows them to execute /bin/bash with root privileges.
```

Five different prompts for a three-step privilege escalation scenario built for a workshop about learning linux privilge escalation. An example of a two-step scenario might be:
```
Login via SSH using given credentials for username “petr”. (petr:zotzotzot3!)
Use binary capabilities (cap_dac_read) on VI to read some other user’s sensitive file at: /home/{username}/creds.txt (username randomized by GoE) 
Use stolen credentials to login as the other user. Get flag at /home/{username}/flag.txt  `flag{example_flag_1}`
Use misconfigured NOPASSWD awk sudo privileges to escalate to root and read /root/flag.txt containing `flag{example_flag_2}`
```

The available privesc techniques are:
Overly Permissive sudo permissions on a binary/user 
  /etc/sudoers
Bash history leaks
File Permissions
  SUID
  Capabilities
Hijacking Jobs
  Cronjobs
SSH Keys
  With an example of chaining vulnerabilities to read keys for a different user


Scenarios. - overfitted
```
Scenario 1 — Capabilities → Bash History → NOPASSWD Sudo
Login via SSH as "petr" (petr:zotzotzot3).

python3 has the cap_dac_read_search capability set. Use it to bypass file permission
checks and read /home/wren/flag1.txt. flag{python_cap_reads_wren_9f2a}

Use the same capability to read /home/wren/.bash_history. It contains the command
`su - dex` followed by dex's password (dex:gr4vel0ad!). Switch to dex and read
/home/dex/flag2.txt. flag{wren_history_gives_up_dex_4c7b}

dex has a NOPASSWD sudo entry for /usr/bin/awk in /etc/sudoers. Use awk to read
/root/flag3.txt. flag{dex_awk_sudo_owns_root_1e8d}
```

Scenario - further generalized, allow GoE to make decisions about which techniques to use, but still have a clear path to root:
```
Create a 3-stage Linux privilege escalation challenge on a single Ubuntu machine. The initial foothold must be a low-privilege SSH account with the username 'petr' and password 'zotzotzot3!'.

Autonomously design an exploit chain that requires the operator to pivot through at least one intermediate user before achieving root execution. You must randomly select and logically chain together exactly three distinct privilege escalation vectors from the following pool:
 - Misconfigured sudo permissions
 - Bash history leaks
 - SUID binaries or Linux Capabilities
 - Hijacking scheduled Cronjobs
 - Leaked or accessible SSH keys (e.g., chaining a vulnerability to read another user's key)
 - Exposed environment variables containing sensitive information

Generate all intermediate usernames, script names, and system misconfigurations autonomously. Inject exactly three unique flags in the format flag{auto_generated_string_n} where n is a number corresponding to the stage, placing one at the successful completion of each of the three stages.
```

Scenario - further generalized, allow GoE to make decisions about which techniques to use, but still have a clear path to root:
```
Create a 3-stage Linux privilege escalation challenge on a single Ubuntu machine. The initial foothold must be a low-privilege SSH account with the username 'petr' and password 'zotzotzot3!'. The operator must discover and exploit a chain of three distinct privilege escalation vectors to achieve root execution. Each stage should result in gaining access to a new user account with its own home directory. Please only choose from the following techniques: misconfigured sudoers file, bash history leaks, SUID binaries or Linux Capabilities, hijacking scheduled Cronjobs, and exposed environment variables. Please be creative with passwords and usernames.
```

Create a 3-stage Linux privilege escalation challenge on a single Ubuntu machine. The initial foothold must be a low-privilege SSH account with the username 'petr' and password 'zotzotzot3!'. The operator must discover and exploit a chain of three distinct privilege escalation vectors to achieve root execution. Each stage should result in gaining access to a new user account with its own home directory. Please only choose from the following techniques: misconfigured sudoers file, bash history leaks, SUID binaries or Linux Capabilities. Please be creative with passwords and usernames

```
I want an ubuntu system with a vulnerable website. It should be one PHP-based website with an arbitrary file upload vulnerability, where the uploads directory is not placed in the webroot. However, you can use LFI from a seperate endpoint to access the uploads directory and execute any uploaded webshells. This should lead to remote code execution as the www-data user.
```

```
A Node.js/Express support ticket portal where users submit bug reports and an admin reviews them. The admin bot visits submitted tickets in a headless browser. The ticket content is not sanitized, allowing XSS to steal the admin's session cookie. Avoid any post-exploitation goals, this is purely an XSS lab.
```