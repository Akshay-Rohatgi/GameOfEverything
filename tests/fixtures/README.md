# GoE v2 Test Fixtures

## Overview

This directory contains entity fixtures for testing the GoE v2 build pipeline. Each fixture represents a single vulnerable entity (web app or OS-level misconfiguration) that can be built, deployed, and validated through L2 testing.

## Directory Structure

```
fixtures/
├── entities/          # Entity YAML files (web app vulnerabilities)
├── golden_plans/      # Golden plans for planning eval
└── FIXTURE_MATRIX.md  # Coverage matrix and gap analysis
```

## Using Fixtures

### Single Entity Test
```bash
.venv/bin/python -m goe.build --spec tests/fixtures/entities/sqli_express.yaml
```

### Multi-Entity Eval
```bash
.venv/bin/python -m goe.eval --suite build \
  --fixtures tests/fixtures/entities/sqli_express.yaml,tests/fixtures/entities/cmdi_flask.yaml
```

### Validate All Fixtures
```bash
./scripts/validate_all_fixtures.sh        # Test all fixtures
./scripts/validate_all_fixtures.sh --quick  # Test only confirmed-passing
```

## Fixture Format

Each entity fixture is a minimal YAML file:

```yaml
id: sqli_express_entity
description: Express web application with SQL injection vulnerability
system_id: target_system
runtime: express  # express | flask | apache_php | ubuntu
requires: []      # Incoming edges (empty for single-entity)
provides: []      # Outgoing edges (empty for terminal entities)
atoms:
  - sqli_union    # Vulnerability atom from atoms/web_vulnerabilities/
```

## Available Runtimes

### Web Runtimes
- **express** — Node.js 20 + Express.js
- **flask** — Python 3 + Flask
- **apache_php** — Apache 2 + PHP

### System Runtime
- **ubuntu** — Ubuntu 22.04 for OS-level misconfigurations

## Confirmed Passing Fixtures ✅

These fixtures have been validated through L2 testing:

| Fixture | Runtime | Atom | Status |
|---------|---------|------|--------|
| `sqli_express.yaml` | Express | sqli_union | ✅ Confirmed |
| `cmdi_flask.yaml` | Flask | cmd_injection | ✅ Confirmed |
| `sqli_php.yaml` | PHP | sqli_union | ✅ Confirmed |
| `xss_stored_php.yaml` | PHP | xss_stored | ✅ Confirmed |
| `xss_admin_bot_express.yaml` | Express | xss_admin_bot | ✅ Confirmed |

## Complete Fixture Inventory

### SQL Injection (sqli_union)
- ✅ `sqli_express.yaml` — Confirmed passing
- ⚠️ `sqli_flask.yaml` — Untested
- ✅ `sqli_php.yaml` — Confirmed passing

### Command Injection (cmd_injection)
- ⚠️ `cmdi_express.yaml` — Untested
- ✅ `cmdi_flask.yaml` — Confirmed passing
- ⚠️ `cmdi_php.yaml` — Untested

### Stored XSS (xss_stored)
- ⚠️ `xss_express.yaml` — Untested
- ⚠️ `xss_stored_flask.yaml` — Untested
- ✅ `xss_stored_php.yaml` — Confirmed passing

### Reflected XSS (xss_reflected)
- ⚠️ `xss_reflected_express.yaml` — Untested
- ⚠️ `xss_reflected_flask.yaml` — Untested
- 🆕 `xss_reflected_php.yaml` — New, untested

### XSS with Admin Bot (xss_admin_bot)
- ✅ `xss_admin_bot_express.yaml` — Confirmed passing
- 🆕 `xss_admin_bot_flask.yaml` — New, untested
- 🆕 `xss_admin_bot_php.yaml` — New, untested

### Path Traversal/LFI (path_traversal_lfi)
- ⚠️ `path_traversal_express.yaml` — Untested
- ⚠️ `path_traversal_flask.yaml` — Untested
- 🆕 `path_traversal_php.yaml` — New, untested

### Server-Side Template Injection (ssti_jinja2)
- N/A (Express) — Not applicable
- ⚠️ `ssti_flask.yaml` — Untested (Flask-specific)
- N/A (PHP) — Not applicable

### File Upload (file_upload_bypass)
- 🔲 Missing
- 🔲 Missing
- ⚠️ `file_upload_php.yaml` — Untested

### Insecure Deserialization (insecure_deserialization)
- 🔲 Missing
- ⚠️ `insecure_deserialization_flask.yaml` — Untested
- 🔲 Missing

## Coverage Statistics

- **Total fixtures:** 21 (17 existing + 4 new)
- **Confirmed passing:** 5 (24%)
- **Untested:** 13 (62%)
- **Missing:** 3 (14%)

### By Runtime
- **Express:** 6/10 atoms available (60%)
- **Flask:** 7/10 atoms available (70%)
- **PHP:** 8/10 atoms available (80%)

## Creating New Fixtures

1. **Choose atom and runtime**
   ```bash
   ls atoms/web_vulnerabilities/  # See available atoms
   ```

2. **Create fixture file**
   ```yaml
   # tests/fixtures/entities/my_vuln_runtime.yaml
   id: my_vuln_runtime_entity
   description: Runtime web application with my vulnerability
   system_id: target_system
   runtime: express  # or flask or apache_php
   requires: []
   provides: []
   atoms:
     - my_atom_id
   ```

3. **Test the fixture**
   ```bash
   .venv/bin/python -m goe.build --spec tests/fixtures/entities/my_vuln_runtime.yaml
   ```

4. **Add to confirmed list** (if L2 passes)
   - Update this README
   - Update CLAUDE.md
   - Add to CI test suite

## Fixture Testing Batches

### Quick Validation (Confirmed Only)
```bash
.venv/bin/python -m goe.eval --suite build --fixtures \
  tests/fixtures/entities/sqli_express.yaml,\
  tests/fixtures/entities/cmdi_flask.yaml,\
  tests/fixtures/entities/sqli_php.yaml,\
  tests/fixtures/entities/xss_stored_php.yaml,\
  tests/fixtures/entities/xss_admin_bot_express.yaml
```

### Core Vulnerabilities (SQLi + CMDi)
```bash
.venv/bin/python -m goe.eval --suite build --fixtures \
  tests/fixtures/entities/sqli_express.yaml,\
  tests/fixtures/entities/sqli_flask.yaml,\
  tests/fixtures/entities/sqli_php.yaml,\
  tests/fixtures/entities/cmdi_express.yaml,\
  tests/fixtures/entities/cmdi_flask.yaml,\
  tests/fixtures/entities/cmdi_php.yaml
```

### XSS Complete Suite
```bash
.venv/bin/python -m goe.eval --suite build --fixtures \
  tests/fixtures/entities/xss_express.yaml,\
  tests/fixtures/entities/xss_stored_flask.yaml,\
  tests/fixtures/entities/xss_stored_php.yaml,\
  tests/fixtures/entities/xss_reflected_express.yaml,\
  tests/fixtures/entities/xss_reflected_flask.yaml,\
  tests/fixtures/entities/xss_reflected_php.yaml,\
  tests/fixtures/entities/xss_admin_bot_express.yaml
```

## Next Steps

1. **Validate existing fixtures**
   ```bash
   ./scripts/validate_all_fixtures.sh
   ```

2. **Create missing fixtures**
   - `file_upload_express.yaml`
   - `file_upload_flask.yaml`
   - `insecure_deserialization_express.yaml`

3. **Expand atom coverage**
   - Add blind SQLi fixtures (sqli_blind)
   - Add JWT weakness fixtures (jwt_weak_secret)
   - Add TOCTOU race condition fixtures

4. **Establish baseline**
   ```bash
   .venv/bin/python -m goe.eval --suite build \
     --fixtures tests/fixtures/entities/*.yaml \
     --output baselines/baseline_$(date +%Y%m%d)
   ```

## Troubleshooting

**Fixture fails L2 test?**
1. Check atom definition in `atoms/web_vulnerabilities/<atom>.md`
2. Review runtime rules in `goe/runtimes/templates/<runtime>.yaml`
3. Run with verbose output: `--verbose` flag or check `output/<timestamp>.log`

**Want to understand what the crew generated?**
1. Check `output/<timestamp>_deploy.sh` for the deploy script
2. Look at crew outputs in the build log
3. Use eval system to see per-agent token usage

**Need to compare runtimes?**
Run eval with multiple fixtures and check per-entity breakdown to see token/latency differences.

## Related Documentation

- `FIXTURE_MATRIX.md` — Complete coverage matrix and gap analysis
- `atoms/web_vulnerabilities/` — Atom definitions
- `goe/runtimes/templates/` — Runtime specifications
- `EVAL_TEST_RESULTS.md` — Example eval results
