# GoE Build Package

## Request

> web app with SQL injection leaking DB credentials, then SSH pivot to a separate database server

## System

Systems: web_server, db_server

## Entities

| Entity | Runtime | Atoms | Status | Attempts |
| --- | --- | --- | --- | --- |
| sqli_credential_dump | apache_php | sqli_union | PASSED | 2 |

## Edge Chain

- `operator` → `sqli_credential_dump` (network_reach)
- `sqli_credential_dump` → `ssh_pivot_db_server` (creds_for)
- `sqli_credential_dump` → `ssh_pivot_db_server` (shell_as)

## Running (multi-system)

Start all systems:

```bash
docker-compose up -d
```

Or deploy each system manually:

```bash
bash web_server_deploy.sh  # system: web_server (webserver)
```
```bash
bash db_server_deploy.sh  # system: db_server (dbserver)
```

The end-to-end attack chain is in `chain_playbook.yaml`.
Per-entity attack steps are in `playbook.yaml`.
