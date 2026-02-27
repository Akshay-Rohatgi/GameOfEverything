---
id: set_suid
description: Sets the SUID bit on a binary to allow execution as the file owner (usually root).
required_vars: [binary_path]
---
# Atom: Set SUID Bit
A classic Unix-based privilege escalation vector.

### Logic Requirements:
1. Ensure the binary is owned by root: `chown root:root <binary_path>`
2. Set the SUID bit: `chmod u+s <binary_path>`

### Common Patterns:
- **Vulnerable Find:** `chmod u+s /usr/bin/find`
- **Vulnerable Bash:** `cp /bin/bash /tmp/bash && chmod +s /tmp/bash`

### Testing Guidance:
1. Verify the binary is owned by root: `ls -l <binary_path>` should show `root` as the owner.
2. Verify the SUID bit is set: `ls -l <binary_path>` should show an `s` in the user permissions (e.g., `-rwsr-xr-x`).

### Synthesis Guidance:
Provide the full path to the binary. The Builder should choose a binary that makes sense for the "Context" (e.g., a "troubleshooting tool" left by an admin). For example, a software developer may leave behind an editing tool like `vim` with the SUID bit set. But a system administrator may leave behind `apt` or `busybox`. 