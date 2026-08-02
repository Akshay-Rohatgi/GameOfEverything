---
id: xss_reflected
type: web_vulnerability
description: A reflected cross-site scripting vulnerability where a URL parameter is echoed unsanitized into the HTML response, causing any JavaScript in the parameter to execute immediately in the browser.
required_vars: []
---
# Atom: Reflected XSS

A URL query parameter (or POST field) is embedded directly into the HTML response without HTML encoding. Unlike stored XSS, the payload is not persisted — it exists only in the URL and fires when a victim clicks a crafted link. In demo environments, confirmed by requesting the URL directly and observing the unencoded payload in the response.

### Logic Requirements:
1. A GET (or POST) parameter is read and embedded into the HTML response body.
2. No HTML encoding (`htmlspecialchars()`, `escape()`, `encodeURIComponent()`) is applied before rendering.
3. The parameter lands in a context that allows script execution — inside a `<div>`, `<p>`, or similar body element (not inside an attribute, which requires a different breakout).
4. The reflection is immediate — the same request that sends the payload receives the response containing it.

### Synthesis Guidance:
The natural cover story is a search results page, an error message, or a personalised greeting where the user's input is echoed back. The reflected value appears in something like "Search results for: <input>" or "Hello, <name>!".

**PHP (Apache):**
```php
$search = $_GET['q'];
echo "<h2>Results for: " . $search . "</h2>";  // ← no htmlspecialchars()
```
Payload: `?q=<script>alert(document.cookie)</script>`

**Python / Flask:**
```python
query = request.args.get('q', '')
return f"<h2>Results for: {query}</h2>"  # ← f-string, no escaping
```
Note: Flask's `render_template` auto-escapes — use an f-string response or `Markup()` to create the vulnerability. Do NOT use `render_template` with a `{{ variable }}` unless `|safe` is applied.
Payload: `?q=<script>alert(1)</script>`

**Node.js / Express:**
```js
const q = req.query.q || '';
res.send(`<h2>Results for: ${q}</h2>`);  // ← template literal, no encoding
```
Payload: `?q=<script>alert(1)</script>`

### Testing Guidance:
**Layer 1 — Internal state check:**
Confirm the payload is reflected unencoded:
```bash
curl -s "http://localhost/app?q=<script>alert(1)</script>" | grep "<script>alert(1)</script>"
```
The response must contain the literal `<script>` tag rather than `&lt;script&gt;`.

**Layer 2 — External attack probe (from attacker container):**
```bash
curl -s "http://target/app?q=%3Cscript%3Ealert%281%29%3C%2Fscript%3E" | grep -i "<script>"
```
A successful result shows the unencoded `<script>` tag in the HTML source, confirming reflected XSS.
