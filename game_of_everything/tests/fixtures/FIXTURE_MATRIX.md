# GoE v2 Fixture Matrix

## Confirmed Passing ✅

According to CLAUDE.md, these fixtures are confirmed to pass L2 testing:

- `sqli_express.yaml` ✅
- `cmdi_flask.yaml` ✅
- `sqli_php.yaml` ✅
- `xss_stored_php.yaml` ✅
- `xss_admin_bot_express.yaml` ✅

## Complete Matrix: Runtime × Atom

| Atom | Express | Flask | PHP | Status |
|------|---------|-------|-----|--------|
| **sqli_union** | ✅ sqli_express | ✅ sqli_flask | ✅ sqli_php | All tested |
| **cmd_injection** | ✅ cmdi_express | ✅ cmdi_flask | ✅ cmdi_php | All available |
| **xss_stored** | ✅ xss_express | ✅ xss_stored_flask | ✅ xss_stored_php | All available |
| **xss_reflected** | ✅ xss_reflected_express | ✅ xss_reflected_flask | ❌ Missing | Need PHP |
| **xss_admin_bot** | ✅ xss_admin_bot_express | ❌ Missing | ❌ Missing | Need Flask/PHP |
| **path_traversal_lfi** | ✅ path_traversal_express | ✅ path_traversal_flask | ❌ Missing | Need PHP |
| **ssti_jinja2** | ❌ N/A | ✅ ssti_flask | ❌ N/A | Flask-only |
| **file_upload_bypass** | ❌ Missing | ❌ Missing | ✅ file_upload_php | Need Express/Flask |
| **insecure_deserialization** | ❌ Missing | ✅ insecure_deserialization_flask | ❌ Missing | Need Express/PHP |
| **sqli_blind** | ❌ Missing | ❌ Missing | ❌ Missing | None |
| **sqli_tautology** | ❌ Missing | ❌ Missing | ❌ Missing | None |
| **jwt_weak_secret** | ❌ Missing | ❌ Missing | ❌ Missing | None |
| **toctou_race_condition** | ❌ Missing | ❌ Missing | ❌ Missing | None |

## Coverage Summary

### By Runtime
- **Express:** 6/13 atoms (46%)
- **Flask:** 7/13 atoms (54%)  
- **PHP:** 4/13 atoms (31%)

### By Atom
- **Complete coverage (3 runtimes):** 3 atoms (sqli_union, cmd_injection, xss_stored)
- **Partial coverage (1-2 runtimes):** 5 atoms
- **No coverage:** 5 atoms

### Total Fixtures
- **Available:** 17 fixtures
- **Confirmed passing:** 5 fixtures (29%)
- **Untested:** 12 fixtures (71%)

## Priority: Fixtures to Test Next

### High Priority (Complete Runtime Coverage)
These atoms have 2/3 runtimes, adding the third gives complete coverage:

1. `xss_reflected_php.yaml` — Complete xss_reflected coverage
2. `path_traversal_php.yaml` — Complete path_traversal_lfi coverage
3. `xss_admin_bot_flask.yaml` — Extend admin bot testing
4. `xss_admin_bot_php.yaml` — Complete admin bot coverage

### Medium Priority (Extend Atom Coverage)
Test existing fixtures to confirm they pass:

5. `cmdi_express.yaml` — Confirm cmd_injection on Express
6. `sqli_flask.yaml` — Confirm SQLi on Flask
7. `xss_stored_flask.yaml` — Confirm stored XSS on Flask

### Low Priority (New Atom Types)
Create and test entirely new atom types:

8. `sqli_blind_*.yaml` — Blind SQL injection
9. `jwt_weak_secret_*.yaml` — JWT vulnerabilities
10. `file_upload_express.yaml` — File upload on Express

## Recommended Test Batches

### Batch 1: Complete Core Coverage (High Value)
```bash
.venv/bin/python -m goe.eval --suite build --fixtures \
  tests/fixtures/entities/sqli_express.yaml,\
  tests/fixtures/entities/sqli_flask.yaml,\
  tests/fixtures/entities/sqli_php.yaml,\
  tests/fixtures/entities/cmdi_express.yaml,\
  tests/fixtures/entities/cmdi_flask.yaml,\
  tests/fixtures/entities/cmdi_php.yaml
```
**Goal:** Validate all SQLi and Command Injection across all runtimes

### Batch 2: XSS Complete Coverage
```bash
.venv/bin/python -m goe.eval --suite build --fixtures \
  tests/fixtures/entities/xss_stored_php.yaml,\
  tests/fixtures/entities/xss_stored_flask.yaml,\
  tests/fixtures/entities/xss_express.yaml,\
  tests/fixtures/entities/xss_reflected_express.yaml,\
  tests/fixtures/entities/xss_reflected_flask.yaml,\
  tests/fixtures/entities/xss_admin_bot_express.yaml
```
**Goal:** Comprehensive XSS testing

### Batch 3: Runtime-Specific Vulns
```bash
.venv/bin/python -m goe.eval --suite build --fixtures \
  tests/fixtures/entities/ssti_flask.yaml,\
  tests/fixtures/entities/insecure_deserialization_flask.yaml,\
  tests/fixtures/entities/file_upload_php.yaml
```
**Goal:** Test runtime-specific vulnerabilities

## Creating Missing Fixtures

To create a missing fixture (e.g., `xss_reflected_php.yaml`):

```yaml
id: xss_reflected_php_entity
description: PHP web application with reflected XSS vulnerability
system_id: target_system
runtime: apache_php
requires: []
provides: []
atoms:
  - xss_reflected
```

Key fields:
- `id`: Unique identifier (use `<atom>_<runtime>_entity` pattern)
- `runtime`: `express`, `flask`, or `apache_php`
- `atoms`: List with one atom ID from `atoms/web_vulnerabilities/`

## Usage Recommendations

### For Development
Test a diverse subset covering all runtimes:
```bash
.venv/bin/python -m goe.eval --suite build --fixtures \
  tests/fixtures/entities/sqli_express.yaml,\
  tests/fixtures/entities/cmdi_flask.yaml,\
  tests/fixtures/entities/xss_stored_php.yaml
```

### For CI/Regression
Test all confirmed-passing fixtures:
```bash
.venv/bin/python -m goe.eval --suite build --fixtures \
  tests/fixtures/entities/sqli_express.yaml,\
  tests/fixtures/entities/cmdi_flask.yaml,\
  tests/fixtures/entities/sqli_php.yaml,\
  tests/fixtures/entities/xss_stored_php.yaml,\
  tests/fixtures/entities/xss_admin_bot_express.yaml
```

### For Comprehensive Baseline
Test ALL available fixtures (may take 10-15 minutes):
```bash
.venv/bin/python -m goe.eval --suite build \
  --fixtures tests/fixtures/entities/*.yaml
```

## Next Steps

1. **Batch test existing fixtures** to identify which are confirmed passing
2. **Create missing high-priority fixtures** (xss_reflected_php, path_traversal_php, etc.)
3. **Update CLAUDE.md** with expanded confirmed-passing list
4. **Establish baseline** with all confirmed fixtures for regression testing
5. **Track coverage over time** as new atoms/runtimes are added
