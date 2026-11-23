---
id: cms_default_creds
description: Deploys a CMS (WordPress or Joomla) with weak or default administrator credentials, providing an authenticated web application entry point exploitable via the admin panel.
required_vars: [cms_type, admin_user, admin_password]
---
# Atom: CMS Default Credentials
A content management system is installed with a weak or default administrator password. Attackers can log in to the admin panel and leverage it for Remote Code Execution via plugin/theme upload, template editing, or built-in file editors.

### Logic Requirements:
1. Install dependencies: Apache/Nginx, PHP, MySQL. Install distro-aware (apt vs dnf) — on
   RHEL/Rocky the web package is `httpd`, the DB is `mariadb-server`, and PHP runs through
   **`php-fpm`** (there is no `mod_php` on RHEL 9; httpd's shipped `php.conf` already routes
   to the php-fpm socket), so start `php-fpm` alongside `httpd`. No systemd — start daemons
   directly.
2. Create a MySQL database and user for the CMS.
3. Download and configure the CMS with `<admin_user>` and `<admin_password>` as the administrator credentials.
4. Start the web server and database.

### Common Patterns:

**WordPress (primary target):**
```bash
# --- Install + start the LAMP stack (distro-aware; no systemd in these containers) ---
# Debian uses in-process mod_php; RHEL 9 has NO mod_php — PHP runs via php-fpm, which
# httpd's default /etc/httpd/conf.d/php.conf already routes to. Branch on the package
# manager, not the OS name.
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y apache2 libapache2-mod-php php php-mysql php-curl php-gd php-mbstring php-xml mariadb-server curl
  WWW_USER=www-data
  mysqld_safe & 
  apachectl start 2>/dev/null || apache2ctl start
else
  # RHEL/Rocky — EPEL already enabled by the base bootstrap. (curl is already present as
  # curl-minimal; don't install `curl` here — it conflicts without --allowerasing.)
  dnf -y install httpd php php-fpm php-mysqlnd php-gd php-mbstring php-xml mariadb-server
  WWW_USER=apache
  [ -d /var/lib/mysql/mysql ] || mariadb-install-db --user=mysql --datadir=/var/lib/mysql >/dev/null 2>&1
  mysqld_safe &
  mkdir -p /run/php-fpm && php-fpm -D        # PHP handler for httpd (no mod_php on RHEL 9)
  httpd -k start
fi
# Wait for MariaDB to accept connections before creating the DB.
for i in $(seq 1 30); do mysqladmin ping --silent 2>/dev/null && break; sleep 1; done

# Create DB
mysql -e "CREATE DATABASE wordpress; CREATE USER 'wp'@'localhost' IDENTIFIED BY 'wppassword'; GRANT ALL ON wordpress.* TO 'wp'@'localhost'; FLUSH PRIVILEGES;"

# Download and configure WordPress
cd /var/www/html
curl -s https://wordpress.org/latest.tar.gz | tar xz
mv wordpress/* .
cp wp-config-sample.php wp-config.php
sed -i "s/database_name_here/wordpress/; s/username_here/wp/; s/password_here/wppassword/; s/localhost/127.0.0.1/" wp-config.php

# Install WordPress via WP-CLI (headless, avoids web installer; php CLI works on both families)
curl -sO https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar
chmod +x wp-cli.phar
./wp-cli.phar core install --path=/var/www/html \
  --url="http://localhost" \
  --title="Corp Blog" \
  --admin_user="<admin_user>" \
  --admin_password="<admin_password>" \
  --admin_email="admin@example.com" \
  --allow-root
# NOTE: admin_email must be a valid address with a TLD — WP-CLI rejects admin@localhost.
# Serve files as the distro's web user (www-data on Debian, apache on RHEL).
chown -R "$WWW_USER":"$WWW_USER" /var/www/html
```

**Joomla:**
```bash
# Install + start the LAMP stack with the same distro-aware preamble as WordPress above
# (apt → apache2/mod_php/mysql-start; dnf → httpd + php-fpm + mariadb-install-db + httpd -k start).
mysql -e "CREATE DATABASE joomla; CREATE USER 'joomla'@'localhost' IDENTIFIED BY 'joomlapass'; GRANT ALL ON joomla.* TO 'joomla'@'localhost'; FLUSH PRIVILEGES;"
# Download Joomla
curl -sL https://downloads.joomla.org/cms/joomla5/5-1-0/Joomla_5-1-0-Stable-Full_Package.tar.gz | tar xz -C /var/www/html/
# Joomla still requires web-based or CLI install; use joomla-cli if available
```

### Testing Guidance:
1. Verify the web server is running: `curl -s http://127.0.0.1/ | grep -i "wordpress\|joomla\|wp-login"`
2. Verify admin login:
   - WordPress: `curl -s -c /tmp/wpcookies.txt -b /tmp/wpcookies.txt -X POST http://127.0.0.1/wp-login.php -d 'log=<admin_user>&pwd=<admin_password>&wp-submit=Log+In&redirect_to=%2Fwp-admin%2F&testcookie=1' -D - | grep -i 'location\|dashboard'`
3. From outside: browse to `http://<container_ip>/wp-login.php` and log in with the configured credentials.
4. Verify RCE potential: navigate to Appearance > Theme Editor or Plugins > Plugin Editor and confirm PHP file editing is available.

### Synthesis Guidance:
WordPress is the primary and easiest to automate target (WP-CLI enables headless install). Joomla and Drupal require more complex installation steps. The Builder should use WP-CLI for WordPress automation. Ensure the webroot is owned by the
distro's web user (`www-data` on Debian, `apache` on RHEL/Rocky) so the server can serve the
files. On RHEL/Rocky remember PHP is served by `php-fpm` (start it; no `mod_php`). Use `<admin_password>` values from `rockyou.txt` (via `get_rockyou_password()`) for realism. Common defaults: `admin/admin`, `admin/password`, `admin/wordpress`.
