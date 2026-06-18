# GoE Build Package

## Request

> Express app with SQLi leaking SSH password, then sudo privesc to root

## System

Systems: target_system

## Entities

| Entity | Runtime | Atoms | Status | Attempts |
| --- | --- | --- | --- | --- |
| sqli_ssh_credential_leak | express | sqli_union | PASSED | 1 |
| ssh_login_leaked_creds | ubuntu | — | PASSED | 2 |
| sudo_privesc_root | ubuntu | — | PASSED | 1 |

## Edge Chain

- `operator` → `sqli_ssh_credential_leak` (network_reach)
- `sqli_ssh_credential_leak` → `ssh_login_leaked_creds` (creds_for)
- `operator` → `ssh_login_leaked_creds` (network_reach)
- `ssh_login_leaked_creds` → `sudo_privesc_root` (shell_as)

## Running

Deploy the full single-box environment:

```bash
bash deploy.sh
```

Attack steps for each entity are in `playbook.yaml` (executable via the GoE procedure runner).
