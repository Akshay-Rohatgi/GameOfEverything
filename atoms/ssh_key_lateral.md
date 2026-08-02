---
id: ssh_key_lateral
type: misconfig
description: An SSH private key for a user on a different system is left readable in a privileged location (e.g. /root/.ssh/), enabling lateral movement when an attacker reaches root.
required_vars: []
---

# Atom: SSH Key Lateral Movement

An SSH private key for a user on a remote system is stored in a privileged directory (typically `/root/.ssh/`) on the current system. Once an attacker gains root, they can read the key and SSH to the remote system without a password.

### Logic Requirements:

1. The entity that **stores/exposes** the key:
   - Places the pre-materialized private key at a path under `/root/.ssh/` (e.g. `/root/.ssh/id_rsa_<user>`)
   - Must provide a `creds_for` edge with `cred_type: ssh_key` — the build system pre-generates the keypair so producer and consumer share identical key material
   - Does NOT create the user on the remote system — that belongs to the consuming entity

2. The entity that **authorizes** the key on the remote system:
   - Creates the target user account
   - Installs the matching public key into `~<user>/.ssh/authorized_keys`
   - Requires the same `creds_for` (ssh_key) edge

### Edge Contract:

The key pair flows through a `creds_for` edge:
- `cred_type`: `ssh_key`
- `user`: the username on the remote system
- `secret`: base64-encoded OpenSSH private key (pre-materialized by orchestrator — do NOT run ssh-keygen)
- `host`: the remote system's hostname

**Producer** (entity that stores/exposes the key) sets:
```bash
echo '<secret_b64>' | base64 -d > /root/.ssh/id_rsa_<user>
chmod 600 /root/.ssh/id_rsa_<user>
```

**Consumer** (entity that authorizes the key on remote system) sets:
```bash
useradd -m -s /bin/bash <user>
echo '<secret_b64>' | base64 -d > /tmp/k && chmod 600 /tmp/k
install -d -m700 /home/<user>/.ssh
ssh-keygen -y -f /tmp/k > /home/<user>/.ssh/authorized_keys
chown -R <user>:<user> /home/<user>/.ssh
rm /tmp/k
```

### Testing Guidance (producer entity):

```bash
# Verify key is present and readable by root
ls -la /root/.ssh/id_rsa_<user>
# Verify key material is valid
head -1 /root/.ssh/id_rsa_<user>
```
Expected: `-----BEGIN OPENSSH PRIVATE KEY-----`

### Testing Guidance (consumer entity):

```bash
# Verify user exists and authorized_keys is installed
ls -la /home/<user>/.ssh/authorized_keys
ssh -i /root/.ssh/id_rsa_<user> -o StrictHostKeyChecking=no -o BatchMode=yes <user>@localhost 'id'
```
Expected: `uid=...(<user>)`

### Synthesis Guidance:

- The **producer** entity's `provides` list must include a `creds_for` edge (ssh_key type). Use a descriptive edge id like `dev_ssh_key_<system>`.
- The **consumer** entity's `requires` list must include that same edge id.
- Never run `ssh-keygen` to create a new keypair — the orchestrator pre-generates it and injects `secret` as base64 key material into both entities via edge propagation.
- The key file path on the producer side (e.g. `/root/.ssh/id_rsa_dev`) is local color only — the actual key material travels through the edge.
