# GoE Build Package

## Request

> Ubuntu server with SSH weak password admin:password

## System

Systems: target_system

## Entities

| Entity | Runtime | Atoms | Status | Attempts |
| --- | --- | --- | --- | --- |
| ssh_weak_password | ubuntu | — | PASSED | 2 |

## Edge Chain

- `operator` → `ssh_weak_password` (network_reach)

## Running

Deploy the full single-box environment:

```bash
bash deploy.sh
```

Attack steps for each entity are in `playbook.yaml` (executable via the GoE procedure runner).
