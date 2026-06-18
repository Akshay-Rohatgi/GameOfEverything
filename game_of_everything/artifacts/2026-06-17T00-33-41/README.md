# GoE Build Package

## Request

> Ubuntu with weak SSH and sudo misconfiguration to root

## System

Systems: target_system

## Entities

| Entity | Runtime | Atoms | Status | Attempts |
| --- | --- | --- | --- | --- |
| ssh_weak_credentials | ubuntu | — | PASSED | 2 |
| sudo_privesc_root | ubuntu | — | PASSED | 1 |

## Edge Chain

- `operator` → `ssh_weak_credentials` (network_reach)
- `ssh_weak_credentials` → `sudo_privesc_root` (shell_as)

## Running

Deploy the full single-box environment:

```bash
bash deploy.sh
```

Attack steps for each entity are in `playbook.yaml` (executable via the GoE procedure runner).
