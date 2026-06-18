# GoE Build Package

## Request

> web app with SQL injection that leaks credentials

## System

Systems: target_system

## Entities

| Entity | Runtime | Atoms | Status | Attempts |
| --- | --- | --- | --- | --- |
| sqli_login | apache_php | sqli_union | PASSED | 1 |
| ssh_login_with_creds | ubuntu | — | PASSED | 1 |

## Edge Chain

- `operator` → `sqli_login` (network_reach)
- `sqli_login` → `ssh_login_with_creds` (creds_for)

## Running

Deploy the full single-box environment:

```bash
bash deploy.sh
```

Attack steps for each entity are in `playbook.yaml` (executable via the GoE procedure runner).
