# Contributing to Game of Everything

We welcome contributions from the broader community — whether you are fixing a bug, adding a new atom, or improving documentation.

---

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Reporting Issues](#reporting-issues)
- [Contributing Changes](#contributing-changes)
  - [Branching](#branching)
  - [Commit Messages](#commit-messages)
  - [Pull Requests](#pull-requests)
- [Development Setup](#development-setup)
- [Running Tests](#running-tests)
- [Project Areas](#project-areas)
- [Research & Attribution](#research--attribution)

---

## Code of Conduct

This project is a research and educational effort. All contributors are expected to engage respectfully and constructively. Harassment, discrimination, or hostile behavior of any kind will not be tolerated.

---

## Getting Started

1. **Fork** the repository and clone your fork locally.
2. Follow the [setup instructions in the README](README.md#installation) to install dependencies and configure your environment.
3. Confirm your setup works by running the test suite (see [Running Tests](#running-tests)).
4. Find something to work on — open issues, items labeled `good first issue`, or reach out to a maintainer.

---

## Reporting Issues

Before opening a new issue, search existing issues to avoid duplicates.

When filing a bug, include:
- A clear, descriptive title
- Steps to reproduce (including the prompt/scenario used if applicable)
- Expected vs. actual behavior
- Relevant log output (found in `output/<timestamp>.log`) — redact any credentials or AWS keys
- Environment: OS, Python version, Docker version, AWS region

For feature requests, describe the use case and the problem it solves rather than jumping straight to a proposed solution.

---

## Contributing Changes

### Branching

Branch off `main` for new work. Use the same `type/short-description` pattern used elsewhere in this repo:

```
feat/add-postgres-atom
fix/chain-test-edge-propagation
docs/update-contributing
chore/bump-boto3
```

### Commit Messages

Follow the [Conventional Commits](https://www.conventionalcommits.org/) format already established in this repository:

```
type(scope): short imperative description
```

| Type | When to use |
|---|---|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `test` | Adding or updating tests |
| `refactor` | Code restructure with no behavior change |
| `chore` | Build, deps, tooling, CI |

**Examples from this repo:**
```
feat(tui): tui added + diagnostician improvements
fix(attacker_container): ensure attacker container has necessary tools for testing loop
docs(CLAUDE.md): update for Phase 4 completion + Phase 5 roadmap
test(procedures): tests for basic execution, SSH logins, step chaining, and SUID privesc
```

Keep the subject line under 72 characters. Use the body for _why_, not _what_.

### Pull Requests

- Target `main` unless a maintainer directs otherwise.
- PR titles follow the same `type(scope): description` format as commits.
- Keep PRs focused — one logical change per PR.
- Fill out the PR description with a summary of what changed and why.
- All CI checks must pass before review.
- At least one maintainer approval is required to merge.

For large changes (new pipeline phase, new runtime, significant refactor), open an issue or draft PR first to discuss the approach before investing significant time.

---

## Development Setup

```bash
cd game_of_everything
uv sync
source .venv/bin/activate
```

Configure your environment:

```bash
cp goe.toml.example goe.toml
# Fill in AWS credentials and model settings
```

Verify Bedrock access and ingest the RAG atom database before running the full pipeline:

```bash
python scripts/bedrock_access.py \
  us.anthropic.claude-sonnet-4-6-20251001-v1:0 \
  us.anthropic.claude-opus-4-6-v1:0 \
  amazon.titan-embed-text-v2:0
python scripts/rag_gen.py
```

See the [README](README.md) for full setup and command documentation.

---

## Running Tests

The test suite is split by marker to separate fast unit tests from slow Docker/LLM tests:

```bash
# Fast unit tests (no Docker, no AWS)
.venv/bin/python -m pytest -m "not docker and not llm"

# Docker integration tests (~1-3 min each)
.venv/bin/python -m pytest -m docker

# Full suite (requires Docker + AWS credentials)
.venv/bin/python -m pytest tests/
```

New features should include corresponding tests. Bug fixes should include a test that would have caught the bug.

---

## Project Areas

| Area | Location | Notes |
|---|---|---|
| planner | `goe/planner/` | NL → EntityGraph; LLM prompts in `prompts/` |
| construction crew | `goe/construction_crew/` | Engineer / Developer / Attacker agents |
| flow & orchestration | `goe/flow/` | Run/resume pipeline, chain test |
| retry & diagnostics | `goe/retry/` | Failure categorization and agent routing |
| Misconfig/privesc atoms | `atoms/` | Markdown + YAML frontmatter |
| Web vulnerability atoms | `atoms/web_vulnerabilities/` | Markdown + YAML frontmatter |
| Runtime templates | `goe/runtimes/templates/` | Per-runtime install/start/healthcheck |
| Docker images | `docker/` | Target and attacker Dockerfiles |

When adding a new atom or runtime, follow the patterns documented in [CLAUDE.md](CLAUDE.md).

---

## Research & Attribution

If you use this project in academic work, please cite it appropriately and reach out to the maintainers — we welcome collaboration and co-authorship opportunities with researchers building on this work.

Contributions are accepted under the project's [GPLv3 license](LICENSE). By submitting a pull request you agree that your contributions will be licensed under the same terms.
