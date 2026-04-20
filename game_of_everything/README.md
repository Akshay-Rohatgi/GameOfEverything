# Game of Everything (GoE)

Game of Everything is an agentic framework for building vulnerable cybersecurity environments on demand. Describe a scenario in plain English — GoE parses the request, maps it to a library of vulnerability atoms, generates and validates bash deploy scripts inside Docker containers, and optionally deploys everything to AWS EC2.

## Prerequisites

- Python 3.10–3.13
- [uv](https://docs.astral.sh/uv/) (`pip install uv`)
- Docker (running locally — required for script testing)
- AWS credentials with access to Amazon Bedrock (`claude-3-*` and `amazon.titan-embed-text-v2:0`)

## Installation

```bash
cd game_of_everything
uv sync          # installs all dependencies into .venv
```

Or via crewAI's own helper:

```bash
crewai install
```

## Configuration

Copy `.env` and fill in your values:

```bash
cp .env .env.local   # optional; GoE loads .env automatically
```

| Variable | Required | Description |
|---|---|---|
| `AWS_REGION` | Yes | AWS region for Bedrock and EC2 (e.g. `us-east-1`) |
| `AWS_ACCESS_KEY_ID` | Yes | AWS access key |
| `AWS_SECRET_ACCESS_KEY` | Yes | AWS secret key |
| `GOE_ATTACKER_CIDR` | For EC2 deploys | Your IP in CIDR notation (`x.x.x.x/32`) |
| `GOE_EC2_REGION` | No | Override EC2 region (defaults to `AWS_REGION`) |
| `GOE_EC2_INSTANCE_TYPE` | No | EC2 instance type (default `t3.small`) |
| `GOE_EC2_TTL_HOURS` | No | Auto-destroy TTL in hours (default `4`, `0` = disabled) |

## RAG Setup (one-time)

The mapping agents use ChromaDB + Bedrock Titan embeddings. Ingest the atoms library before your first run:

```bash
python scripts/rag_gen.py
```

Re-run this whenever you add or modify atom files under `atoms/`.

To verify retrieval is working:

```bash
python scripts/query.py "weak password login"
python scripts/query.py "sql injection login" --collection web_vuln_atoms
python scripts/query.py "samba share" --n 5
```

## Commands

All commands are run from the `game_of_everything/` directory with the virtualenv active.

### `run_crew` / `kickoff` — Generate a vulnerable environment

Starts the full GoE pipeline. You will be prompted to describe the environment you want.

```bash
run_crew
# or
crewai run
```

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--resume CHECKPOINT_DIR` | — | Resume a previously interrupted run from its checkpoint directory (e.g. `output/.checkpoints/20260418_200538`) |
| `--deploy ec2` | — | After generation, deploy the scenario to AWS EC2 |
| `--review` | off | After script generation, pause for interactive per-box review before deploying |
| `--ec2-region REGION` | `us-east-1` | AWS region for EC2 deployment |
| `--ec2-instance-type TYPE` | `t3.small` | EC2 instance type |
| `--ec2-attacker-cidr CIDR` | `$GOE_ATTACKER_CIDR` | Your IP in CIDR notation — required for `--deploy ec2` |
| `--ec2-ttl-hours N` | `4` | Auto-destroy TTL in hours (`0` = no auto-destroy) |

**Examples:**

```bash
# Basic generation (local Docker testing only)
run_crew

# Generate and immediately deploy to EC2
run_crew --deploy ec2 --ec2-attacker-cidr 203.0.113.5/32

# Resume an interrupted run and deploy
run_crew --resume output/.checkpoints/20260418_200538 --deploy ec2 --ec2-attacker-cidr 203.0.113.5/32

# Generate with interactive review step before deploying
run_crew --deploy ec2 --review --ec2-attacker-cidr 203.0.113.5/32
```

---

### `goe-deploy` — Deploy an existing output directory to EC2

Skips generation entirely and deploys a previously generated output directory.

```bash
goe-deploy output/20260418_200538_<scenario_slug> --attacker-cidr 203.0.113.5/32
```

**Options:**

| Argument / Flag | Description |
|---|---|
| `output_dir` (positional) | Path to the output directory containing `playbook.json` and `*_deploy.sh` files |
| `--region REGION` | AWS region (default: `$GOE_EC2_REGION` or `us-east-1`) |
| `--instance-type TYPE` | EC2 instance type (default: `$GOE_EC2_INSTANCE_TYPE` or `t3.small`) |
| `--attacker-cidr CIDR` | Your IP in CIDR notation — required |
| `--ttl-hours N` | Auto-destroy TTL in hours (default: `$GOE_EC2_TTL_HOURS` or `4`) |

---

### `goe-destroy` — Tear down an EC2 deployment

Destroys all EC2 infrastructure (VPC, instances, security groups) for a given run.

```bash
goe-destroy 20260418_200538
```

**Options:**

| Argument / Flag | Description |
|---|---|
| `run_id` (positional) | The run ID to destroy (the `YYYYMMDD_HHMMSS` prefix of the output folder name) |
| `--region REGION` | AWS region (default: `$GOE_EC2_REGION` or `us-east-1`) |

---

### `plot` — Visualize the flow

Renders the GoE agent flow graph to `goe_flow.png`.

```bash
plot
```

---

## Pipeline Overview

GoE runs these steps in order:

1. **synthesize_scenario** — Parses the request and designs the multi-box network topology.
2. **box_pipelines** — For each box in parallel: maps atoms → validates mapping → enumerates dependencies → sequences atoms → generates bash snippets → tests snippets in Docker.
3. **chain_test** — (Multi-box only) validates the end-to-end attack chain across boxes.
4. **finalize_topology** — (Multi-box only) writes the output package (`output/<run_id>_<slug>/`).
5. **review_and_fix** — (Optional, `--review`) interactive review loop for failed snippets.
6. **deploy_ec2** — (Optional, `--deploy ec2`) provisions EC2 instances via Terraform and runs the deploy scripts.

Checkpoints are saved after each step to `output/.checkpoints/<run_id>/`. Use `--resume` to restart from the last completed step.

## Output

Each run produces a timestamped directory under `output/`:

```
output/20260418_200538_<scenario_slug>/
├── playbook.json          # topology metadata and attack narrative
├── docker-compose.yml     # local Docker environment
├── README.md              # scenario-specific notes
├── <box1>_deploy.sh       # hardened deploy script for box 1
└── <box2>_deploy.sh       # hardened deploy script for box 2
```

## Extending the Atoms Library

Atoms are Markdown files in `atoms/` (misconfiguration) or `atoms/web_vulnerabilities/` (web vulns). Each file has a YAML frontmatter block followed by description, required variables, synthesis guidance, and testing guidance.

After adding or editing an atom, re-ingest the RAG database:

```bash
python scripts/rag_gen.py
```

## Verify Bedrock Access

```bash
python scripts/bedrock_access.py
```
