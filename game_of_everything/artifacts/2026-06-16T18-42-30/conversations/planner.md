# Conversation: planner

**Model**: us.anthropic.claude-sonnet-4-6  
**Calls**: 5

## System Prompt

```
You are a penetration testing scenario designer. Given a user's natural language request for a cybersecurity challenge, design the infrastructure (systems/machines) needed to implement the scenario.

Output a JSON array of System objects. Each system represents one machine in the scenario.

Schema for each system:
```json
{
  "id": "snake_case_identifier",
  "os": "ubuntu_22_04",
  "services": ["list", "of", "service", "names"],
  "network": {
    "hostname": "short_hostname",
    "exposed_ports": [80, 443],
    "internal_ports": [3306, 5432]
  }
}
```

Rules:
- `id` must be unique snake_case (e.g. `web_server`, `db_server`)
- `hostname` must be short, DNS-safe (e.g. `webserver`, `dbserver`, `target`)
- `exposed_ports` are reachable by the attacker; `internal_ports` are only reachable within the scenario network
- `services` is a human-readable list (e.g. ["web", "database", "ssh"])
- For single-machine scenarios, use one system with `id: target_system` and `hostname: target`
- Common OS is `ubuntu_22_04` unless the request specifies otherwise

**Port-to-runtime mapping** (for web apps):
- Express web apps bind to port **3000**
- Flask web apps bind to port **5000**
- PHP/Apache web apps bind to port **80**
- SSH service uses port **22**

If the scenario involves web vulnerabilities, expose the appropriate web port. If it involves SSH login/pivot, expose port 22.

## Examples

### Example 1: Single web application
**Request:** "Create a SQL injection challenge where the attacker extracts credentials"

**Output:**
```json
[{
  "id": "target_system",
  "os": "ubuntu_22_04",
  "services": ["web", "database"],
  "network": {
    "hostname": "target",
    "exposed_ports": [3000],
    "internal_ports": [3306]
  }
}]
```

### Example 2: Multi-system lateral movement
**Request:** "Build a scenario where the attacker compromises a web app on one server and pivots to a database server via SSH"

**Output:**
```json
[
  {
    "id": "web_system",
    "os": "ubuntu_22_04",
    "services": ["web"],
    "network": {
      "hostname": "webserver",
      "exposed_ports": [80],
      "internal_ports": []
    }
  },
  {
    "id": "db_system",
    "os": "ubuntu_22_04",
    "services": ["ssh", "database"],
    "network": {
      "hostname": "dbserver",
      "exposed_ports": [22],
      "internal_ports": [3306]
    }
  }
]
```

Output ONLY valid JSON — no markdown, no explanation, no surrounding text. Output the raw JSON array.

```

## Turn 1 — `planner.design_systems` (user)

**user:**

```
Design the infrastructure systems needed for this attack scenario:

Ubuntu server with SSH weak password admin:password
```

## Turn 2 — `planner.design_systems` (assistant, in 773 tok / out 47 tok)

**assistant:**

```
[{"id":"target_system","os":"ubuntu_22_04","services":["ssh"],"network":{"hostname":"target","exposed_ports":[22],"internal_ports":[]}}]
```

## Turn 3 — `planner.plan_entities` (user)

**user:**

```
## User Request

Ubuntu server with SSH weak password admin:password

## Available Systems

```json
[
  {
    "id": "target_system",
    "os": "ubuntu_22_04",
    "services": [
      "ssh"
    ],
    "network": {
      "hostname": "target",
      "exposed_ports": [
        22
      ],
      "internal_ports": []
    }
  }
]
```

Decompose this scenario into entity stubs.
```

## Turn 4 — `planner.plan_entities` (assistant, in 1868 tok / out 95 tok)

**assistant:**

```
```json
[
  {
    "id": "ssh_weak_password",
    "description": "SSH service on port 22 accepts weak credentials admin:password; attacker brute-forces or guesses the password and gains shell access as the admin user",
    "system_id": "target_system",
    "runtime": "ubuntu",
    "atoms": []
  }
]
```
```

## Turn 5 — `planner.grade_stubs` (user)

**user:**

```
## Systems

```json
[
  {
    "id": "target_system",
    "os": "ubuntu_22_04",
    "services": [
      "ssh"
    ],
    "network": {
      "hostname": "target",
      "exposed_ports": [
        22
      ],
      "internal_ports": []
    }
  }
]
```

## Entity Stubs to Grade

```json
[
  {
    "id": "ssh_weak_password",
    "description": "SSH service on port 22 accepts weak credentials admin:password; attacker brute-forces or guesses the password and gains shell access as the admin user",
    "system_id": "target_system",
    "runtime": "ubuntu",
    "atoms": []
  }
]
```

Grade and correct these stubs. Return the corrected JSON array.
```

## Turn 6 — `planner.grade_stubs` (assistant, in 1956 tok / out 90 tok)

**assistant:**

```
[
  {
    "id": "ssh_weak_password",
    "description": "SSH service on port 22 accepts weak credentials admin:password; attacker brute-forces or guesses the password and gains shell access as the admin user",
    "system_id": "target_system",
    "runtime": "ubuntu",
    "atoms": []
  }
]
```

## Turn 7 — `planner.specify_entities` (user)

**user:**

```
## User Request

Ubuntu server with SSH weak password admin:password

## Available Systems

```json
[
  {
    "id": "target_system",
    "os": "ubuntu_22_04",
    "services": [
      "ssh"
    ],
    "network": {
      "hostname": "target",
      "exposed_ports": [
        22
      ],
      "internal_ports": []
    }
  }
]
```

## Entity Stubs to Specify

```json
[
  {
    "id": "ssh_weak_password",
    "description": "SSH service on port 22 accepts weak credentials admin:password; attacker brute-forces or guesses the password and gains shell access as the admin user",
    "system_id": "target_system",
    "runtime": "ubuntu",
    "atoms": []
  }
]
```

Fully specify ALL entities in a single JSON array. Edge IDs must be consistent across all entities: every edge_id that appears in one entity's `provides` must appear verbatim in the downstream entity's `requires`, and vice versa. Invent the edge IDs once and reuse them — do not use different IDs for the same relationship.
```

## Turn 8 — `planner.specify_entities` (assistant, in 2175 tok / out 129 tok)

**assistant:**

```
```json
[
  {
    "id": "ssh_weak_password",
    "description": "SSH service on port 22 accepts weak credentials admin:password; attacker brute-forces or guesses the password and gains shell access as the admin user",
    "system_id": "target_system",
    "runtime": "ubuntu",
    "atoms": [],
    "requires": [
      {"edge_id": "op_to_ssh", "optional": false}
    ],
    "provides": []
  }
]
```
```

## Turn 9 — `planner.connect_edges` (user)

**user:**

```
## Entities

```json
[
  {
    "id": "ssh_weak_password",
    "description": "SSH service on port 22 accepts weak credentials admin:password; attacker brute-forces or guesses the password and gains shell access as the admin user",
    "system_id": "target_system",
    "runtime": "ubuntu",
    "requires": [
      {
        "edge_id": "op_to_ssh",
        "optional": false
      }
    ],
    "provides": [],
    "atoms": []
  }
]
```

## Systems

```json
[
  {
    "id": "target_system",
    "os": "ubuntu_22_04",
    "services": [
      "ssh"
    ],
    "network": {
      "hostname": "target",
      "exposed_ports": [
        22
      ],
      "internal_ports": []
    }
  }
]
```

Create the Edge objects that wire these entities together.
```

## Turn 10 — `planner.connect_edges` (assistant, in 1837 tok / out 104 tok)

**assistant:**

```
```json
[
  {
    "id": "op_to_ssh",
    "from_entity": "operator",
    "to_entity": "ssh_weak_password",
    "type": "network_reach",
    "params": {
      "host": {"structural": "target", "concrete": null},
      "port": {"structural": "22", "concrete": null}
    }
  }
]
```
```

