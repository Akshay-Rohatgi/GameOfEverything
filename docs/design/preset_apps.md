# Plan: Pre-Built Web Application Support (Preset Apps)

## Context

GoE supports misconfig atoms (small config tweaks) and custom apps (LLM-generated single-file web apps). We want support for **real-world web applications** with intentional vulnerabilities, starting with **WordPress** and **phpBB**.

The hard problem is **testing**: multi-step attack chains through real web apps require stateful HTTP session handling. Solution: per-app **bash helper libraries** staged onto the attacker container, plus **app CLI tools** (WP-CLI) or **direct DB queries** for L1 internal checks.

Both apps share the LAMP stack (Apache + PHP + MariaDB), validating stack deduplication from day one.

---

## Architecture: New `PresetAppFlow` Pipeline

Parallel to `CustomAppFlow` with:
- **Template-driven deploy snippets** — deterministic bash, no LLM
- **Bash helper libraries per app** — staged onto attacker for reliable multi-step L2 testing
- **WP-CLI (WordPress) / direct DB+curl (phpBB) for L1 testing**

### Why a new pipeline

- **Not atoms**: Can't split a WordPress install across incremental atoms
- **Not CustomAppFlow**: No app code to generate — install recipe is deterministic

---

## Testing Strategy

### WordPress

**L1 (Internal):** WP-CLI on target — queries DB/filesystem directly:
```bash
wp user list --allow-root --path=/var/www/html
wp post list --post_status=any --allow-root --path=/var/www/html
wp plugin list --status=active --allow-root --path=/var/www/html
```

**L2 (External):** `wp_helpers.sh` on attacker (~40-50 lines):
- `wp_login url user pass` — cookie-based session, returns success/fail
- `wp_get_nonce url` — extracts REST API nonce
- `wp_api_get url endpoint` — authenticated REST API call
- `wp_find_in_posts url pattern` — search post content via REST API
- `wp_check_endpoint url path` — check HTTP status of arbitrary path

### phpBB

**L1 (Internal):** Direct MariaDB queries + curl on target:
```bash
mysql -u phpbb -pPASS phpbb_db -e "SELECT username, user_password FROM phpbb_users WHERE group_id=5"
curl -s http://127.0.0.1/phpBB3/ | grep -i "phpbb"
curl -s http://127.0.0.1/phpBB3/adm/ | grep -i "admin"
```

**L2 (External):** `phpbb_helpers.sh` on attacker (~40-50 lines):
- `phpbb_login url user pass` — form POST to `ucp.php?mode=login`, manages SID cookie
- `phpbb_get_sid url` — extract session ID from cookies
- `phpbb_admin_login url user pass` — login to ACP (`adm/index.php`)
- `phpbb_list_users url sid` — scrape/query user listing
- `phpbb_check_endpoint url path` — HTTP status check

### Harness staging

1. New `copy_to_attacker()` method on `TestEnvironmentTool` (mirrors `copy_to_target()`)
2. `PresetAppFlow` stages the appropriate harness before L2 testing
3. Harness files live in `preset_apps/harnesses/`

---

## Data Model

### New models (`models.py`)

```python
class PresetVector(BaseModel):
    preset_id: str                          # "wordpress", "phpbb"
    vuln_profile_ids: List[str]             # ["wp_default_creds"]
    port: int = 80
    admin_user: Optional[str] = None
    admin_password: Optional[str] = None
    db_name: Optional[str] = None
    db_user: Optional[str] = None
    db_password: Optional[str] = None
    synthesis_context: str = ""
    extra_vars: dict = {}

class ResolvedPresetApp(BaseModel):
    vector: PresetVector
    stack_id: str
    deploy_snippet: str
    testing_snippet: str
    attack_snippet: str
    validation_passed: bool
```

### Modified existing models

- `SynthesizedScenario`: add `preset_vectors: List[PresetVector] = []`
- `BoxSpec`: add `preset_vectors: List[PresetVector] = []`
- `GoEState`: add `resolved_preset_apps: List[ResolvedPresetApp] = []`

---

## YAML Definitions

### Directory structure
```
src/game_of_everything/preset_apps/
  stacks/
    lamp.yaml
  presets/
    wordpress.yaml
    phpbb.yaml
  vuln_profiles/
    wp_default_creds.yaml
    phpbb_default_creds.yaml
  harnesses/
    wp_helpers.sh
    phpbb_helpers.sh
```

### Stack: `lamp.yaml`
```yaml
id: lamp
packages: [apache2, php, libapache2-mod-php, php-mysql, php-curl, php-gd,
           php-mbstring, php-xml, php-zip, php-json, mariadb-server, curl, ca-certificates]
install_snippet: |
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends {{packages}}
  service mariadb start
  for i in $(seq 1 15); do mysqladmin ping -s 2>/dev/null && break; sleep 1; done
  service apache2 start
  sleep 1
db_setup_template: |
  mysql -e "CREATE DATABASE IF NOT EXISTS {{db_name}};"
  mysql -e "CREATE USER IF NOT EXISTS '{{db_user}}'@'localhost' IDENTIFIED BY '{{db_password}}';"
  mysql -e "GRANT ALL ON {{db_name}}.* TO '{{db_user}}'@'localhost'; FLUSH PRIVILEGES;"
```

### Preset: `wordpress.yaml`
```yaml
id: wordpress
stack_id: lamp
required_vars: [admin_user, admin_password, db_name, db_user, db_password]
defaults:
  db_name: wordpress
  db_user: wp
  db_password: wpdbpass123
  site_title: "Corporate Blog"
install_template: |
  cd /var/www/html && rm -f index.html
  curl -sL https://wordpress.org/latest.tar.gz | tar xz
  mv wordpress/* . && rmdir wordpress
  cp wp-config-sample.php wp-config.php
  sed -i "s/database_name_here/{{db_name}}/; s/username_here/{{db_user}}/; s/password_here/{{db_password}}/; s/localhost/127.0.0.1/" wp-config.php
  chown -R www-data:www-data /var/www/html
  curl -sO https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar
  chmod +x wp-cli.phar && mv wp-cli.phar /usr/local/bin/wp
  wp core install --path=/var/www/html \
    --url="http://localhost" --title="{{site_title}}" \
    --admin_user="{{admin_user}}" --admin_password="{{admin_password}}" \
    --admin_email="admin@localhost" --allow-root
healthcheck: |
  for i in $(seq 1 30); do
    curl -s -o /dev/null -w '%{http_code}' http://localhost/ 2>/dev/null | grep -qE '200|302' && break
    sleep 2
  done
harness_id: wp_helpers
```

### Preset: `phpbb.yaml`
```yaml
id: phpbb
stack_id: lamp
required_vars: [admin_user, admin_password, db_name, db_user, db_password]
defaults:
  db_name: phpbb_db
  db_user: phpbb
  db_password: phpbbpass123
  board_name: "Company Forum"
install_template: |
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends php-intl
  cd /tmp
  curl -sL https://download.phpbb.com/pub/release/3.3/3.3.11/phpBB-3.3.11.tar.bz2 | tar xj
  mv phpBB3 /var/www/html/phpBB3
  chown -R www-data:www-data /var/www/html/phpBB3
  chmod 666 /var/www/html/phpBB3/config.php
  # Headless install via CLI installer
  cd /var/www/html/phpBB3
  php install/phpbbcli.php install \
    --board-url="http://localhost/phpBB3" \
    --board-name="{{board_name}}" \
    --board-description="Internal Discussion" \
    --db-driver="mysqli" --db-host="127.0.0.1" \
    --db-name="{{db_name}}" --db-user="{{db_user}}" --db-passwd="{{db_password}}" \
    --db-table-prefix="phpbb_" \
    --admin-name="{{admin_user}}" --admin-pass1="{{admin_password}}" \
    --admin-pass2="{{admin_password}}" --admin-email="admin@localhost" \
    --no-interaction
  chmod 640 /var/www/html/phpBB3/config.php
  chown www-data:www-data /var/www/html/phpBB3/config.php
  rm -rf /var/www/html/phpBB3/install
healthcheck: |
  for i in $(seq 1 20); do
    curl -s -o /dev/null -w '%{http_code}' http://localhost/phpBB3/ 2>/dev/null | grep -q '200' && break
    sleep 2
  done
harness_id: phpbb_helpers
```

### Vuln profile: `wp_default_creds.yaml`
```yaml
id: wp_default_creds
compatible_presets: [wordpress]
vuln_config_snippet: ""  # Weak creds ARE the vulnerability
testing_snippet_template: |
  wp user list --fields=user_login --allow-root --path=/var/www/html | grep "{{admin_user}}"
  wp eval 'if(wp_check_password("{{admin_password}}", get_user_by("login","{{admin_user}}")->user_pass)) echo "AUTH_OK";' --allow-root --path=/var/www/html | grep "AUTH_OK"
attack_snippet_template: |
  source /tmp/harnesses/wp_helpers.sh
  wp_login "http://target:{{port}}" "{{admin_user}}" "{{admin_password}}" && echo "WP_LOGIN_SUCCESS"
```

### Vuln profile: `phpbb_default_creds.yaml`
```yaml
id: phpbb_default_creds
compatible_presets: [phpbb]
vuln_config_snippet: ""
testing_snippet_template: |
  mysql -u {{db_user}} -p{{db_password}} {{db_name}} -N -e "SELECT username FROM phpbb_users WHERE group_id=5" | grep "{{admin_user}}"
attack_snippet_template: |
  source /tmp/harnesses/phpbb_helpers.sh
  phpbb_login "http://target:{{port}}/phpBB3" "{{admin_user}}" "{{admin_password}}" && echo "PHPBB_LOGIN_SUCCESS"
  phpbb_admin_login "http://target:{{port}}/phpBB3" "{{admin_user}}" "{{admin_password}}" && echo "PHPBB_ACP_SUCCESS"
```

---

## PresetAppFlow (`steps/preset_app_flow.py`)

### Steps

1. **`load_and_render`**
   - Load preset YAML + stack YAML + all selected vuln profile YAMLs
   - Merge defaults with vector vars
   - Render deploy snippet: stack install → DB setup → app install → healthcheck → vuln config
   - Render testing snippet: app-specific L1 checks from vuln profiles
   - Render attack snippet: `source harness` + L2 templates from vuln profiles
   - Template substitution via `str.replace()` on `{{var}}` placeholders

2. **`validate_end_to_end`**
   - Setup Docker test environment
   - Stage harness onto attacker via `copy_to_attacker()`
   - Run deploy snippet in target
   - L1: run testing snippet in target, LLM verdict
   - L2: run attack snippet from attacker, LLM verdict
   - On failure: LLM diagnoses, max 2 retries with fresh containers

3. **`emit_result`**
   - Package into `ResolvedPresetApp`

---

## Files to Modify

| File | Change |
|---|---|
| `src/game_of_everything/models.py` | Add `PresetVector`, `ResolvedPresetApp` |
| `src/game_of_everything/state.py` | Add `resolved_preset_apps` field |
| `src/game_of_everything/models.py` (`SynthesizedScenario`, `BoxSpec`) | Add `preset_vectors` field |
| `src/game_of_everything/steps/run_box_pipelines.py` | Call `run_resolve_preset_apps(state)` after `run_resolve_custom_apps` (line 290) |
| `src/game_of_everything/steps/finalize_script.py` | Include preset app sections + stack dedup logic |
| `src/game_of_everything/config/tasks.yaml` | Extend synthesis task to recognize WP/phpBB requests and emit `preset_vectors` |
| `src/game_of_everything/tools/test_environment.py` | Add `copy_to_attacker()` method |

## Files to Create

| File | Purpose |
|---|---|
| `src/game_of_everything/steps/preset_app_flow.py` | Core flow: load, render, test, emit |
| `src/game_of_everything/steps/resolve_preset_apps.py` | Orchestrator (mirrors `resolve_custom_apps.py`) |
| `src/game_of_everything/preset_apps/stacks/lamp.yaml` | LAMP stack definition |
| `src/game_of_everything/preset_apps/presets/wordpress.yaml` | WordPress install recipe |
| `src/game_of_everything/preset_apps/presets/phpbb.yaml` | phpBB install recipe |
| `src/game_of_everything/preset_apps/vuln_profiles/wp_default_creds.yaml` | WP default creds profile |
| `src/game_of_everything/preset_apps/vuln_profiles/phpbb_default_creds.yaml` | phpBB default creds profile |
| `src/game_of_everything/preset_apps/harnesses/wp_helpers.sh` | WordPress bash helper library |
| `src/game_of_everything/preset_apps/harnesses/phpbb_helpers.sh` | phpBB bash helper library |

---

## Stack Deduplication in `finalize_script.py`

Both WordPress and phpBB use LAMP. When both appear in one scenario, `finalize_script` emits the LAMP stack install once at the top, then each app's install section. Each `ResolvedPresetApp` carries `stack_id` for dedup.

---

## Verification Plan

1. **Template rendering test**: Load WordPress YAML files, render with test vars, verify valid bash output
2. **WordPress Docker test**: `docker run -it ubuntu:22.04 bash` → paste rendered deploy snippet → verify WP responds
3. **phpBB Docker test**: Same for phpBB → verify forum responds at `/phpBB3/`
4. **Harness test**: In running containers, stage helpers on attacker, run attack snippets, verify login works
5. **End-to-end**: `crewai run` with "WordPress blog with default admin credentials" → verify synthesis → PresetAppFlow → final script
6. **Two-app test**: Request both WordPress + phpBB → verify stack dedup in final script
