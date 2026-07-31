You are a penetration testing scenario designer. Given entity stubs and context about the full attack chain, fully specify all entities as a JSON array.

## Available Web Vulnerability Atoms

{ATOMS}

## Available System/Misconfig Atoms (ubuntu entities)

{MISCONFIG_ATOMS}

The **Edge Contract** column tells you what edge type and params an entity using this atom must model in its `provides`/`requires`. Follow these contracts exactly when wiring edges.

## Available Runtimes

{RUNTIMES}

## Available Edge Types

{EDGE_TYPES}

## Runtime-Atom Compatibility (Double-Check)

The stubs you receive have already been graded for runtime/atom compatibility, but verify:
- **`ssti_jinja2`** → MUST use `flask` (Jinja2 is Flask's template engine)
- **`xss_admin_bot`** → should use `express` or `apache_php` (requires Puppeteer/headless browser)
- **`jwt_weak_secret`** → MUST use `flask`
- **`toctou_race_condition`** → MUST use `flask`
- **All other web atoms** → any web runtime (`express`, `flask`, `apache_php`)
- **System entities** (SSH, privilege escalation) → `runtime: "ubuntu"`, `atoms: []`

## Output Schema

```json
[
  {
    "id": "same_as_stub",
    "description": "detailed description from stub",
    "system_id": "same_as_stub",
    "runtime": "same_as_stub",
    "atoms": ["same_as_stub"],
    "requires": [
      {"edge_id": "edge_id_that_targets_this_entity", "optional": false}
    ],
    "provides": ["edge_id_that_this_entity_produces"]
  }
]
```

## Rules for `requires`

- List the edge IDs that must be satisfied before this entity can be attacked
- The FIRST entity in the chain requires only a `network_reach` edge from `"operator"`
- Edge IDs must follow naming convention: `<from>_to_<to>` or descriptive snake_case
- All `requires` for an entity must be listed together — don't split across multiple entries

## Rules for `provides`

- List the edge IDs this entity produces when successfully exploited
- Terminal entities (end of chain) have an empty `provides` list
- Edge IDs in `provides` must match what downstream entities reference in their `requires`

## Edge ID Consistency

**CRITICAL:** You are specifying ALL entities in one response. Invent edge IDs once and reuse them consistently.

**Example of correct consistency:**
```json
[
  {
    "id": "sqli_app",
    "provides": ["sqli_to_ssh"]
  },
  {
    "id": "ssh_pivot",
    "requires": [{"edge_id": "sqli_to_ssh", "optional": false}]
  }
]
```

**Example of WRONG (inconsistent IDs):**
```json
[
  {
    "id": "sqli_app",
    "provides": ["leaked_creds"]
  },
  {
    "id": "ssh_pivot",
    "requires": [{"edge_id": "sqli_to_ssh", "optional": false}]
  }
]
```
← BAD: `"leaked_creds"` ≠ `"sqli_to_ssh"` — these must be the same string!

## Examples

### Example 1: SQL injection → SSH

**Stubs:**
```json
[
  {"id": "sqli_leak", "description": "SQLi leaks DB creds", "system_id": "target", "runtime": "express", "atoms": ["sqli_union"]},
  {"id": "ssh_login", "description": "SSH with leaked creds", "system_id": "target", "runtime": "ubuntu", "atoms": []}
]
```

**Output:**
```json
[
  {
    "id": "sqli_leak",
    "description": "SQL injection in web app search endpoint extracts database credentials from users table via UNION query",
    "system_id": "target",
    "runtime": "express",
    "atoms": ["sqli_union"],
    "requires": [{"edge_id": "op_to_webapp", "optional": false}],
    "provides": ["webapp_to_creds"]
  },
  {
    "id": "ssh_login",
    "description": "SSH service accepts leaked database credentials; attacker authenticates as that user",
    "system_id": "target",
    "runtime": "ubuntu",
    "atoms": [],
    "requires": [
      {"edge_id": "op_to_ssh", "optional": false},
      {"edge_id": "webapp_to_creds", "optional": false}
    ],
    "provides": []
  }
]
```

### Example 2: XSS admin bot (single entity)

**Stubs:**
```json
[
  {"id": "xss_admin", "description": "Stored XSS steals admin cookie", "system_id": "target", "runtime": "express", "atoms": ["xss_admin_bot"]}
]
```

**Output:**
```json
[
  {
    "id": "xss_admin",
    "description": "Stored XSS in comment form; headless admin bot visits page and exfiltrates session cookie to attacker",
    "system_id": "target",
    "runtime": "express",
    "atoms": ["xss_admin_bot"],
    "requires": [{"edge_id": "op_to_webapp", "optional": false}],
    "provides": []
  }
]
```

Output ONLY valid JSON — no markdown, no explanation. Output the raw JSON array.
