---
id: phpmyadmin_disable_auth
description: Installs phpMyAdmin and configures it to allow login without a password (AllowNoPassword) for the MySQL root account, providing unauthenticated web-based database administration.
required_vars: [mysql_root_password]
---
# Atom: phpMyAdmin Disable Auth
phpMyAdmin is configured to permit login with no password (`AllowNoPassword = true`), allowing anyone with HTTP access to the phpMyAdmin interface to administer the MySQL database without credentials.

### Logic Requirements:
1. Install dependencies: `apt-get install -y apache2 php php-mbstring php-zip php-gd php-json php-curl mysql-server phpmyadmin`
2. Configure MySQL root with a known (or empty) password.
3. Configure phpMyAdmin to allow no-password login by editing `config.inc.php`:
   - Set `$cfg['Servers'][$i]['AllowNoPassword'] = true;`
   - Set `$cfg['Servers'][$i]['auth_type'] = 'config';` with `user` = `root` and `password` = `<mysql_root_password>` (or empty)
4. Ensure Apache is started: `service apache2 start`
5. Ensure MySQL is started and root has appropriate access: `service mysql start`

### Common Patterns:
- **No-Password Root via Config Auth (auto-login as root):**
  ```bash
  cat > /etc/phpmyadmin/config.inc.php << 'EOF'
  <?php
  $cfg['blowfish_secret'] = 'notasecret';
  $i = 0;
  $i++;
  $cfg['Servers'][$i]['auth_type'] = 'config';
  $cfg['Servers'][$i]['host'] = '127.0.0.1';
  $cfg['Servers'][$i]['user'] = 'root';
  $cfg['Servers'][$i]['password'] = '';
  $cfg['Servers'][$i]['AllowNoPassword'] = true;
  EOF
  ```
- **Cookie Auth with AllowNoPassword (still requires clicking login):**
  ```bash
  sed -i "s/\$cfg\['Servers'\]\[\$i\]\['AllowNoPassword'\] = false/\$cfg['Servers'][\$i]['AllowNoPassword'] = true/" /etc/phpmyadmin/config.inc.php
  ```
- **Weak Password Configuration:**
  ```bash
  mysql -e "ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY '<mysql_root_password>'; FLUSH PRIVILEGES;"
  # Then configure phpMyAdmin with that password
  ```

### Testing Guidance:
1. Verify Apache is running and phpMyAdmin is accessible: `curl -s http://127.0.0.1/phpmyadmin/ | grep -i phpmyadmin`
2. Verify no-password access: `curl -s -c /tmp/cookies.txt -b /tmp/cookies.txt -X POST http://127.0.0.1/phpmyadmin/index.php -d 'pma_username=root&pma_password=&server=1' | grep -i 'logout\|welcome'`
3. From outside: browse to `http://<container_ip>/phpmyadmin/` — should display the database admin interface without requiring credentials.

### Synthesis Guidance:
This is a heavyweight atom requiring Apache, PHP, MySQL, and phpMyAdmin. The Builder should install all dependencies in a single `apt-get install -y` command to optimize Docker layer caching (see `install_package` atom). phpMyAdmin's Debian package may prompt interactively — use `DEBIAN_FRONTEND=noninteractive` and pre-configure with `debconf-set-selections`. Setting `auth_type = 'config'` provides automatic login (no login screen); `auth_type = 'cookie'` still shows a login form but permits empty passwords.
