# Local Docker deployment

GoE packages can be deployed as persistent local environments with Docker Compose v2.
This is separate from `goe test`, which creates a temporary validation environment and
tears it down after the replay finishes.

## Requirements

- Docker Engine or Docker Desktop with the `docker compose` command
- Local capacity to run one Ubuntu container per scenario system
- The `goe-attacker:latest` image when the package includes an attacker service and
  `solve.sh` (the normal GoE build/test workflow creates this image)

## Deploy

```bash
uv run goe deploy docker output/<run_id>/
```

The command uses the package's `docker-compose.yml`, waits up to ten minutes for every
system provisioning script to finish, and then leaves the containers running. Override
the timeout or Compose project name when needed:

```bash
uv run goe deploy docker output/<run_id>/ --timeout 1200
uv run goe deploy docker output/<run_id>/ --project-name goe-training-lab
```

Each system's declared exposed ports are published with the same host port on
`127.0.0.1` only. Deployment will fail if one of those host ports is already occupied.
Keep these intentionally vulnerable services loopback-only unless you have isolated the
host network and deliberately edit the generated Compose file.

The deployment record is stored at `output/<run_id>/.docker/manifest.json`. A failed
provisioning attempt is preserved for inspection rather than silently removed.

## Inspect and remove

```bash
uv run goe status output/<run_id>/ --provider docker
uv run goe destroy output/<run_id>/ --provider docker
```

`destroy` removes the Compose project's containers, network, and named volumes. It does
not delete the generated output package or Docker images. If an output has only one
deployment provider, `--provider` may be omitted.
