You are a penetration testing scenario architect. Given a set of fully-specified entities, create the Edge objects that wire them together into a valid attack graph.

## Available Edge Types

{EDGE_TYPES}

## When to Use Each Edge Type

- **`network_reach`** — ONLY for `operator` → first_entity (initial access from outside the scenario network)
- **`creds_for`** — one entity leaks credentials consumed by the next entity (SQLi dumps password → SSH login with that password)
- **`shell_as`** — one entity gives interactive shell access as a specific user
- **`token_for`** — one entity captures an auth token/cookie (XSS steals admin session → use that session)
- **`file_write`** — one entity writes a file consumed by the next (file upload → webshell execution)
- **`file_read`** — one entity reads sensitive data from a file (LFI reads config, privesc reads `/etc/shadow`)
- **`code_exec`** — one entity enables code execution in a different runtime context
- **`db_session`** — one entity establishes a database connection used by another

## Output Schema

```json
[
  {
    "id": "op_to_webapp",
    "from_entity": "operator",
    "to_entity": "webapp_entity",
    "type": "network_reach",
    "params": {
      "host": {"structural": "target", "concrete": null},
      "port": {"structural": "3000", "concrete": null}
    }
  }
]
```

## Rules

- Create EXACTLY ONE edge for each unique edge_id referenced in any entity's `requires` or `provides` list
- Use `"from_entity": "operator"` for all initial access edges (network_reach from outside)
- `from_entity` for non-operator edges = the entity ID that has this edge_id in its `provides`
- `to_entity` = the entity ID that has this edge_id in its `requires`
- Terminal edges (in `provides` with no downstream consumer) have `"to_entity": null`
- `params` keys must exactly match the required params for the edge type (see Available Edge Types above)

## Parameter Values (Structural)

Use STRUCTURAL values in params (descriptive identifiers, not concrete values):
- `host`: target system's hostname (e.g. `"target"`, `"webserver"`, `"dbserver"`)
- `port`: port number as a string (e.g. `"3000"`, `"80"`, `"22"`)
- `user`, `as_user`: role descriptor (e.g. `"www-data"`, `"app_user"`, `"db_admin"`)
- `path`: descriptor (e.g. `"config_file"`, `"webshell_path"`, `"shadow_file"`)
- `cred_type`: one of `"password"`, `"ssh_key"`, `"api_key"`
- `runtime`: one of `"node"`, `"python"`, `"php"`
- `service`, `scope`: descriptive strings

**All `concrete` values must be `null`** (filled in after build).

## Examples

### Example 1: SQLi → SSH pivot

**Entities:**
```json
[
  {"id": "sqli_leak", "provides": ["sqli_to_creds"], "requires": [{"edge_id": "op_to_webapp"}]},
  {"id": "ssh_pivot", "provides": [], "requires": [{"edge_id": "op_to_ssh"}, {"edge_id": "sqli_to_creds"}]}
]
```

**Output:**
```json
[
  {
    "id": "op_to_webapp",
    "from_entity": "operator",
    "to_entity": "sqli_leak",
    "type": "network_reach",
    "params": {
      "host": {"structural": "target", "concrete": null},
      "port": {"structural": "3000", "concrete": null}
    }
  },
  {
    "id": "sqli_to_creds",
    "from_entity": "sqli_leak",
    "to_entity": "ssh_pivot",
    "type": "creds_for",
    "params": {
      "user": {"structural": "db_user", "concrete": null},
      "host": {"structural": "target", "concrete": null},
      "cred_type": {"structural": "password", "concrete": null}
    }
  },
  {
    "id": "op_to_ssh",
    "from_entity": "operator",
    "to_entity": "ssh_pivot",
    "type": "network_reach",
    "params": {
      "host": {"structural": "target", "concrete": null},
      "port": {"structural": "22", "concrete": null}
    }
  }
]
```

### Example 2: XSS admin bot (single entity, single edge)

**Entities:**
```json
[
  {"id": "xss_admin", "provides": [], "requires": [{"edge_id": "op_to_webapp"}]}
]
```

**Output:**
```json
[
  {
    "id": "op_to_webapp",
    "from_entity": "operator",
    "to_entity": "xss_admin",
    "type": "network_reach",
    "params": {
      "host": {"structural": "target", "concrete": null},
      "port": {"structural": "3000", "concrete": null}
    }
  }
]
```

## Common Mistakes (DO NOT)

- **DO NOT** use `network_reach` for mid-chain connections — only `operator` → first entity
- **DO NOT** create edges that aren't referenced in any entity's `requires`/`provides`
- **DO NOT** use different edge IDs for the same from/to/type combination
- **DO NOT** set `concrete` values to anything other than `null` at this stage

Output ONLY valid JSON — no markdown, no explanation. Output the raw JSON array.
