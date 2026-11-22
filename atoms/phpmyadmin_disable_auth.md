---
id: phpmyadmin_disable_auth
description: Installs phpMyAdmin and configures it to allow login without a password (AllowNoPassword) for the MySQL root account, providing unauthenticated web-based database administration.
required_vars: [mysql_root_password]
---
# Atom: phpMyAdmin Disable Auth
phpMyAdmin is configured to permit login with no password (`AllowNoPassword = true`), allowing anyone with HTTP access to the phpMyAdmin interface to administer the MySQL database without credentials.

### Logic Requirements:
1. Install dependencies distro-aware (apt vs dnf). Debian: `apache2 libapache2-mod-php php
   php-mbstring php-zip php-gd php-curl mariadb-server phpmyadmin`. RHEL/Rocky: `httpd php
   php-fpm php-mysqlnd php-mbstring php-gd mariadb-server phpMyAdmin` (phpMyAdmin is in EPEL,
   enabled by the base bootstrap). On RHEL there is NO mod_php — PHP runs via `php-fpm`.
2. Configure MySQL root with a known (or empty) password.
3. Configure phpMyAdmin to allow no-password login by editing `config.inc.php` at the distro's
   path (Debian `/etc/phpmyadmin/config.inc.php`, RHEL `/etc/phpMyAdmin/config.inc.php`):
   - Set `$cfg['Servers'][$i]['AllowNoPassword'] = true;`
   - Set `$cfg['Servers'][$i]['auth_type'] = 'config';` with `user` = `root` and `password` = `<mysql_root_password>` (or empty)
4. Start the web server (no systemd): Debian `apachectl start`; RHEL start `php-fpm` then `httpd -k start`.
5. Ensure MySQL is started and root has appropriate access (initialize the datadir with
   `mariadb-install-db` on RHEL, then `mysqld_safe &`).
6. On RHEL, EPEL's `/etc/httpd/conf.d/phpMyAdmin.conf` restricts access to localhost
   (`Require local`) by default — change it to `Require all granted` so the interface is
   reachable over the network. (Both `/phpMyAdmin` and `/phpmyadmin` URL aliases ship.)

### Common Patterns:
- **Install + start + no-password root config (distro-aware):**
  ```bash
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    echo "phpmyadmin phpmyadmin/dbconfig-install boolean false" | debconf-set-selections
    echo "phpmyadmin phpmyadmin/reconfigure-webserver multiselect apache2" | debconf-set-selections
    apt-get update -y
    apt-get install -y apache2 libapache2-mod-php php php-mbstring php-zip php-gd php-curl mariadb-server phpmyadmin
    PMA_CONF=/etc/phpmyadmin/config.inc.php
    mysqld_safe &
    apachectl start 2>/dev/null || apache2ctl start
  else
    dnf -y install httpd php php-fpm php-mysqlnd php-mbstring php-gd mariadb-server phpMyAdmin
    PMA_CONF=/etc/phpMyAdmin/config.inc.php
    sed -i 's/Require local/Require all granted/g' /etc/httpd/conf.d/phpMyAdmin.conf
    [ -d /var/lib/mysql/mysql ] || mariadb-install-db --user=mysql --datadir=/var/lib/mysql >/dev/null 2>&1
    mysqld_safe &
    mkdir -p /run/php-fpm && php-fpm -D
    httpd -k start
  fi
  cat > "$PMA_CONF" << 'EOF'
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
This is a heavyweight atom requiring Apache/httpd, PHP, MariaDB, and phpMyAdmin. Install
dependencies in a single package-manager command (distro-aware: `apt-get` on Debian, `dnf` on
RHEL/Rocky) to optimize Docker layer caching (see `install_package` atom). phpMyAdmin's Debian
package may prompt interactively — use `DEBIAN_FRONTEND=noninteractive` and pre-configure with
`debconf-set-selections`. On RHEL/Rocky, phpMyAdmin comes from **EPEL** (enabled by the base
bootstrap); PHP is served by **`php-fpm`** (no `mod_php`), the config lives at
`/etc/phpMyAdmin/config.inc.php`, and the shipped `/etc/httpd/conf.d/phpMyAdmin.conf` must be
switched from `Require local` to `Require all granted` to be network-reachable. Setting
`auth_type = 'config'` provides automatic login (no login screen); `auth_type = 'cookie'` still
shows a login form but permits empty passwords.
