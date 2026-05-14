# Game of Everything (GoE)

Game of Everything is an agentic framework for building vulnerable cybersecurity environments on demand. Describe a scenario in plain English — GoE parses the request, maps it to a library of vulnerability atoms, generates and validates bash deploy scripts inside Docker containers, and optionally deploys everything to AWS EC2.

**Supported scenario types:**
- Single-box Linux misconfiguration and privilege escalation paths
- Multi-box networked attack chains (lateral movement, pivoting)
- Custom-generated vulnerable web applications (Flask, Express, Apache/PHP)
- Pre-built vulnerable apps (WordPress, phpBB) with configurable vulnerability profiles

---

## Prerequisites

- Python 3.11–3.13
- [uv](https://docs.astral.sh/uv/) (`pip install uv`)
- Docker (running locally — required for script testing)
- AWS account with access to **Amazon Bedrock** (Claude Sonnet/Opus models + `amazon.titan-embed-text-v2:0`)

> **Bedrock model access**: Enable the following models in your AWS Bedrock console before first use:
> `us.anthropic.claude-sonnet-4-6`, `us.anthropic.claude-opus-4-6-v1`, `amazon.titan-embed-text-v2:0`

---

## Installation

```bash
cd game_of_everything
uv sync          # installs all dependencies into .venv
source .venv/bin/activate
```

Or via crewAI's own helper (equivalent):

```bash
crewai install
```

After installing, install the Playwright browser binary (used by the browser-based attack testing):

```bash
playwright install chromium
```

---

## Configuration

GoE is configured via `goe.toml`. Copy the example and fill in your values:

```bash
cp goe.toml.example goe.toml
```

`goe.toml` has four sections:

```toml
[aws]
access_key_id     = ""        # leave blank to use the default AWS credential chain
secret_access_key = ""
region            = "us-east-1"

[models]
default = "anthropic.claude-sonnet-4-6"   # default Bedrock model for all agents

[models.overrides]
# Per-agent overrides — keys match agent names in config/agents.yaml
# app_generation_agent = "anthropic.claude-opus-4-6-v1"

[deploy]
instance_type     = "t3.medium"
key_pair_name     = ""        # required for EC2 deploy
security_group_id = ""        # auto-created if blank
subnet_id         = ""        # auto-selected if blank
```

Environment variables override toml values: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `GOE_DEFAULT_MODEL`, `GOE_MODEL_<AGENT_NAME>`.

### Verify Bedrock Access

```bash
python scripts/bedrock_access.py
```

---

## RAG Setup (one-time)

The mapping agents use ChromaDB with Bedrock Titan embeddings. Ingest the atoms library before your first run:

```bash
python scripts/rag_gen.py
```

This populates two ChromaDB collections:
- `goe_collection` — misconfiguration and privilege escalation atoms
- `web_vuln_atoms` — web vulnerability atoms for custom app generation

Re-run whenever you add or modify files under `atoms/`.

To verify retrieval:

```bash
python scripts/query.py "weak password login"
python scripts/query.py "sql injection login" --collection web_vuln_atoms
python scripts/query.py "samba share" --n 5
```

---

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
| `--ec2-instance-type TYPE` | `t3.medium` | EC2 instance type |
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

| Argument / Flag | Description |
|---|---|
| `output_dir` (positional) | Path to the output directory containing `playbook.json` and `*_deploy.sh` files |
| `--region REGION` | AWS region (default: `$GOE_EC2_REGION` or `us-east-1`) |
| `--instance-type TYPE` | EC2 instance type (default: `$GOE_EC2_INSTANCE_TYPE` or `t3.medium`) |
| `--attacker-cidr CIDR` | Your IP in CIDR notation — required |
| `--ttl-hours N` | Auto-destroy TTL in hours (default: `$GOE_EC2_TTL_HOURS` or `4`) |

---

### `goe-destroy` — Tear down an EC2 deployment

Destroys all EC2 infrastructure (VPC, instances, security groups) for a given run.

```bash
goe-destroy 20260418_200538
```

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

### Docker Image Management

Pre-build the attacker container to separate build failures from test failures:

```bash
python -m game_of_everything.main build_attacker_image
```

Per-runtime target images (`goe-target-express`, `goe-target-flask`, `goe-target-php`) and the browser sidecar image (`goe-browser`) are built automatically on first use. Images are cached for 7 days.

---

### Custom App Test Harness

Standalone test harness for iterating on custom app generation without running the full pipeline:

```bash
# Generate app only (no Docker), save result to file
python scripts/test_custom_app.py --generate-only --save /tmp/app.json

# Run Docker L1+L2 from a previously saved generation (fast iteration)
python scripts/test_custom_app.py --from-file /tmp/app.json --no-rebuild

# Full end-to-end (generate + Docker)
python scripts/test_custom_app.py

# Override the vulnerability, goal, and runtime
python scripts/test_custom_app.py --vuln xss_stored --goal session_theft_via_xss --runtime express
```

Use `--from-file` to iterate on Docker or snippet issues without re-running the expensive LLM generation step.

---

## Pipeline Overview

GoE runs these steps in order:

1. **synthesize_scenario** — Parses the user request and designs the full scenario, resolving implicit decisions and defining the multi-box network topology.
2. **box_pipelines** — For each box (in parallel for multi-box): maps vulnerability atoms → validates mapping → enumerates OS-level dependencies → sequences atoms → generates bash snippets → tests snippets in Docker (Layer 1 + Layer 2).
3. **chain_test** — (Multi-box only) validates the end-to-end attack chain across all boxes (Layer 3).
4. **finalize_topology** — (Multi-box only) writes the output package with docker-compose, per-box scripts, playbook, and README.
5. **review_and_fix** — (Optional, `--review`) interactive per-box review loop for any failed snippets.
6. **deploy_ec2** — (Optional, `--deploy ec2`) provisions EC2 instances and runs deploy scripts.

Checkpoints are saved after each step to `output/.checkpoints/<run_id>/`. Use `--resume` to restart from the last completed step.

### Docker Testing Layers

Each snippet is validated in a multi-container setup on a Docker bridge network:

| Layer | Container | What it checks |
|---|---|---|
| **L1 (Internal)** | `goe_target` (Ubuntu 22.04) | `testing_snippet` — verifies config was applied correctly |
| **L2 (External)** | `goe_attacker` (Kali Linux) | Exploit — verifies the vulnerability is exploitable end-to-end |
| **L2 browser** | `goe_browser` (Playwright/Chromium) | Browser-driven exploits for XSS and other UI-level attacks |
| **L3 (Chain)** | Both | Multi-box only — full attack path validation |

For **custom apps**, L2 is handled by the **Attack Orchestrator** — a single agent with three tools (`exec_in_target`, `exec_in_attacker`, `browser_task`) that autonomously decides the right approach for each exploit. Browser-based scenarios (e.g. stored XSS) automatically use the Playwright browser sidecar. CLI-exploitable scenarios (e.g. SQLi) stay on exec tools.

For **misconfig atoms**, L2 is incremental cumulative: after each snippet is applied, all prior attack probes re-run to catch dependency misordering and regressions. On L1 failure, a Diagnostic Agent attempts up to 2 automated fixes.

---

## Output

Each run produces a timestamped directory under `output/`:

**Single-box:**
```
output/20260418_200538_<scenario_slug>/
└── deploy.sh
```

**Multi-box:**
```
output/20260418_200538_<scenario_slug>/
├── playbook.json          # topology metadata and attack narrative
├── docker-compose.yml     # local Docker environment
├── README.md              # scenario-specific notes
├── <box1>_deploy.sh       # deploy script for box 1
└── <box2>_deploy.sh       # deploy script for box 2
```

Console output is minimal — full agent reasoning is always written to `output/<timestamp>.log`.

---

## Atoms Library

Atoms are the building blocks of GoE scenarios. Each atom is a Markdown file with YAML frontmatter describing a single vulnerability or configuration step.

### Misconfiguration / Privilege Escalation Atoms (`atoms/`)

| Atom ID | Description |
|---|---|
| `bash_history_leak` | Sensitive credentials leaked via `.bash_history` |
| `cms_default_creds` | CMS with default admin credentials |
| `create_user` | Create a local OS user |
| `cron_job_hijack` | Writable script in a cron job |
| `database_expose` | Database listening on a public interface |
| `exposed_env_vars` | Secrets leaked via environment variables |
| `ftp_anon_upload` | Anonymous FTP with write access |
| `insecure_git_repo` | Credentials leaked in git history + world-writable git hooks |
| `install_package` | Install an OS package (infrastructure atom) |
| `mongodb_disable_auth` | MongoDB running without authentication |
| `motd_command_injection` | MOTD script with command injection |
| `phpmyadmin_disable_auth` | phpMyAdmin accessible without login |
| `postgres_rce` | PostgreSQL with `COPY TO/FROM PROGRAM` enabled |
| `python_path_hijack` | Writable directory in a root-owned Python script's path |
| `redis_disable_auth` | Redis with no authentication |
| `redis_replication_leak` | Redis replication leaking data |
| `samba_insecure_share` | Samba share with weak or no authentication |
| `sensitive_file` | World-readable file containing secrets |
| `set_capability` | Binary with dangerous Linux capability |
| `set_suid` | Binary with SUID bit set |
| `sudoers_no_passwd` | `NOPASSWD` sudoers entry |
| `weak_service_password` | Service running with a guessable password |
| `writable_systemd_service` | World-writable systemd unit file |

### Web Vulnerability Atoms (`atoms/web_vulnerabilities/`)

| Atom ID | Description |
|---|---|
| `cmd_injection` | OS command injection |
| `file_upload_bypass` | Unrestricted file upload |
| `insecure_deserialization` | Unsafe deserialization leading to RCE (pickle, PHP unserialize) |
| `jwt_weak_secret` | JWT signed with a guessable secret — forgeable tokens |
| `path_traversal_lfi` | Path traversal / local file inclusion |
| `sqli_blind` | Blind SQL injection |
| `sqli_tautology` | SQL injection via tautology (auth bypass) |
| `sqli_union` | Union-based SQL injection |
| `ssti_jinja2` | Server-side template injection (Jinja2) |
| `toctou_race_condition` | Time-of-check to time-of-use race condition (double-spend / limit bypass) |
| `xss_admin_bot` | Stored XSS with admin bot simulation |
| `xss_reflected` | Reflected XSS |
| `xss_stored` | Stored XSS |

### Adding a New Atom

1. Create `atoms/<atom_id>.md` (or `atoms/web_vulnerabilities/<atom_id>.md`) with frontmatter:
   ```yaml
   ---
   id: atom_id
   required_vars:
     - var_name: description
   ---
   ```
2. Add `Logic Requirements`, `Synthesis Guidance`, and `Testing Guidance` sections.
3. Re-ingest the RAG database:
   ```bash
   python scripts/rag_gen.py
   ```

---

## Custom Applications

GoE can generate fully-functional vulnerable web applications from scratch. The `app_generation_agent` (Claude Opus) produces a single application source file, optional database setup, a deploy snippet, testing snippet, and an `attack_objective` — a structured natural language task that the Attack Orchestrator executes to validate the exploit end-to-end.

**Supported runtimes:** `flask`, `express`, `apache_php`

**Supported attack goals:** `auth_bypass`, `credential_theft`, `lfi_to_rce`, `rce_via_cmd_injection`, `rce_via_sqli`, `rce_via_webshell`, `session_theft_via_xss`, `upload_lfi_rce`

Generated apps are self-contained: the final deploy script embeds all application files as quoted heredocs.

### Attack Orchestrator

Custom app L2 validation uses a single **Attack Orchestrator** agent rather than a static attack script. The orchestrator receives an `attack_objective` (step-by-step natural language task) and routes each step to the appropriate tool:

- `exec_in_attacker` — CLI exploits (curl, sqlmap, ncat)
- `exec_in_target` — white-box checks inside the target container
- `browser_task` — anything requiring a real browser: form submission, JavaScript execution, cookie inspection, XSS triggering

This means browser-dependent attacks (e.g. stored XSS cookie theft) work correctly without any hardcoded attack scripts.

---

## Preset Applications

GoE supports deploying pre-built vulnerable application stacks with configurable vulnerability profiles:

- **WordPress** — with selectable plugins and vulnerability settings
- **phpBB** — forum software with configurable misconfigurations

Preset apps are defined in `src/game_of_everything/preset_apps/presets/`.
