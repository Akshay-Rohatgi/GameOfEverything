#!/bin/bash
# wp_helpers.sh — WordPress attack primitives for L2 testing.
# Source this file from attack snippets: source /tmp/harnesses/wp_helpers.sh

WP_JAR="/tmp/wp_cookies.jar"

wp_login() {
    local url="$1" user="$2" pass="$3"
    rm -f "$WP_JAR"
    local http_code
    http_code=$(curl -s -o /dev/null -w '%{http_code}' \
        -c "$WP_JAR" -b "$WP_JAR" \
        -X POST "${url}/wp-login.php" \
        -d "log=${user}&pwd=${pass}&wp-submit=Log+In&testcookie=1&redirect_to=${url}/wp-admin/" \
        -L --max-redirs 5)
    if [ "$http_code" = "200" ]; then
        echo "Logged in as ${user}"
        return 0
    else
        echo "Login failed (HTTP ${http_code})"
        return 1
    fi
}

wp_get_nonce() {
    local url="$1"
    curl -s -b "$WP_JAR" "${url}/wp-admin/admin-ajax.php?action=rest-nonce" 2>/dev/null
}

wp_api_get() {
    local url="$1" endpoint="$2"
    local nonce
    nonce=$(wp_get_nonce "$url")
    curl -s -b "$WP_JAR" -H "X-WP-Nonce: ${nonce}" "${url}/wp-json/wp/v2${endpoint}"
}

wp_find_in_posts() {
    local url="$1" pattern="$2"
    local posts
    posts=$(wp_api_get "$url" "/posts?status=any&per_page=100&context=edit")
    echo "$posts" | python3 -c "
import sys, json
try:
    posts = json.load(sys.stdin)
except Exception:
    sys.exit(1)
for p in posts:
    raw = p.get('content', {}).get('raw', '')
    rendered = p.get('content', {}).get('rendered', '')
    if '${pattern}' in raw or '${pattern}' in rendered:
        print(raw or rendered)
        sys.exit(0)
print('Pattern not found')
sys.exit(1)
"
}

wp_check_endpoint() {
    local url="$1" path="$2"
    local http_code
    http_code=$(curl -s -o /dev/null -w '%{http_code}' "${url}${path}")
    echo "GET ${path} -> HTTP ${http_code}"
    [ "$http_code" = "200" ]
}

wp_check_admin_access() {
    local url="$1"
    local body
    body=$(curl -s -b "$WP_JAR" -L "${url}/wp-admin/")
    if echo "$body" | grep -qi "dashboard"; then
        echo "Admin dashboard accessible"
        return 0
    else
        echo "Admin dashboard not accessible"
        return 1
    fi
}
