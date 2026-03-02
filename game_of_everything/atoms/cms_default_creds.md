---
id: cms_default_creds
description: Deploys a CMS (WordPress or Joomla) with weak or default administrator credentials, providing an authenticated web application entry point exploitable via the admin panel.
required_vars: [cms_type, admin_user, admin_password]
---
# Atom: CMS Default Credentials
A content management system is installed with a weak or default administrator password. Attackers can log in to the admin panel and leverage it for Remote Code Execution via plugin/theme upload, template editing, or built-in file editors.

### Logic Requirements:
1. Install dependencies: Apache/Nginx, PHP, MySQL.
2. Create a MySQL database and user for the CMS.
3. Download and configure the CMS with `<admin_user>` and `<admin_password>` as the administrator credentials.
4. Start the web server and database.

### Common Patterns:

**WordPress (primary target):**
```bash
apt-get install -y apache2 php php-mysql php-curl php-gd php-mbstring php-xml mysql-server

# Start services
service mysql start
service apache2 start

# Create DB
mysql -e "CREATE DATABASE wordpress; CREATE USER 'wp'@'localhost' IDENTIFIED BY 'wppassword'; GRANT ALL ON wordpress.* TO 'wp'@'localhost'; FLUSH PRIVILEGES;"

# Download and configure WordPress
cd /var/www/html
curl -s https://wordpress.org/latest.tar.gz | tar xz
mv wordpress/* .
cp wp-config-sample.php wp-config.php
sed -i "s/database_name_here/wordpress/; s/username_here/wp/; s/password_here/wppassword/; s/localhost/127.0.0.1/" wp-config.php

# Install WordPress via WP-CLI (headless, avoids web installer)
apt-get install -y curl
curl -sO https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar
chmod +x wp-cli.phar
./wp-cli.phar core install --path=/var/www/html \
  --url="http://localhost" \
  --title="Corp Blog" \
  --admin_user="<admin_user>" \
  --admin_password="<admin_password>" \
  --admin_email="admin@localhost" \
  --allow-root
```

**Joomla:**
```bash
apt-get install -y apache2 php php-mysql php-curl php-gd php-mbstring php-xml mysql-server
service mysql start && service apache2 start
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
WordPress is the primary and easiest to automate target (WP-CLI enables headless install). Joomla and Drupal require more complex installation steps. The Builder should use WP-CLI for WordPress automation. Ensure `chown -R www-data:www-data /var/www/html` is set so Apache can serve the files. Use `<admin_password>` values from `rockyou.txt` (via `get_rockyou_password()`) for realism. Common defaults: `admin/admin`, `admin/password`, `admin/wordpress`.
