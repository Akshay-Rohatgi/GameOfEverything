# GoE Build Package

## Request

> Ubuntu with weak SSH, sudo misconfiguration, and SUID binary for root

## System

Systems: target_system

## Entities

| Entity | Runtime | Atoms | Status | Attempts |
| --- | --- | --- | --- | --- |
| weak_ssh_credentials | ubuntu | — | PASSED | 2 |

## Edge Chain

- `operator` → `weak_ssh_credentials` (network_reach)
- `weak_ssh_credentials` → `(terminal)` (shell_as)

## Running

Deploy the full single-box environment:

```bash
bash deploy.sh
```

Attack steps for each entity are in `playbook.yaml` (executable via the GoE procedure runner).
