#!/bin/bash
# phpbb_helpers.sh — phpBB attack primitives for L2 testing.
# Source this file from attack snippets: source /tmp/harnesses/phpbb_helpers.sh

PHPBB_JAR="/tmp/phpbb_cookies.jar"

phpbb_login() {
    local url="$1" user="$2" pass="$3"
    rm -f "$PHPBB_JAR"
    # Fetch the login page to get the SID and form token
    local login_page
    login_page=$(curl -s -c "$PHPBB_JAR" -b "$PHPBB_JAR" "${url}/ucp.php?mode=login")
    local sid
    sid=$(echo "$login_page" | grep -oP 'name="sid" value="\K[^"]+' | head -1)
    local creation_time
    creation_time=$(echo "$login_page" | grep -oP 'name="creation_time" value="\K[^"]+' | head -1)
    local form_token
    form_token=$(echo "$login_page" | grep -oP 'name="form_token" value="\K[^"]+' | head -1)
    # Submit login form
    local response
    response=$(curl -s -c "$PHPBB_JAR" -b "$PHPBB_JAR" \
        -X POST "${url}/ucp.php?mode=login" \
        -d "username=${user}&password=${pass}&login=Login&sid=${sid}&creation_time=${creation_time}&form_token=${form_token}&redirect=index.php" \
        -D - -o /dev/null -L --max-redirs 5)
    # Check if login succeeded by looking for the user in a follow-up request
    local index_page
    index_page=$(curl -s -b "$PHPBB_JAR" "${url}/index.php")
    if echo "$index_page" | grep -qi "logout\|${user}"; then
        echo "Logged in as ${user}"
        return 0
    else
        echo "Login failed for ${user}"
        return 1
    fi
}

phpbb_get_sid() {
    local url="$1"
    local page
    page=$(curl -s -b "$PHPBB_JAR" "${url}/index.php")
    echo "$page" | grep -oP 'sid=\K[a-f0-9]+' | head -1
}

phpbb_admin_login() {
    local url="$1" user="$2" pass="$3"
    # Must be logged in first as a regular user
    phpbb_login "$url" "$user" "$pass" >/dev/null 2>&1
    local sid
    sid=$(phpbb_get_sid "$url")
    # Access ACP — phpBB re-authenticates for admin panel
    local acp_page
    acp_page=$(curl -s -c "$PHPBB_JAR" -b "$PHPBB_JAR" "${url}/adm/index.php?sid=${sid}")
    local acp_token
    acp_token=$(echo "$acp_page" | grep -oP 'name="form_token" value="\K[^"]+' | head -1)
    local acp_creation
    acp_creation=$(echo "$acp_page" | grep -oP 'name="creation_time" value="\K[^"]+' | head -1)
    # Submit ACP credential form
    local acp_response
    acp_response=$(curl -s -c "$PHPBB_JAR" -b "$PHPBB_JAR" \
        -X POST "${url}/adm/index.php?sid=${sid}" \
        -d "username=${user}&password_${user}=${pass}&login=Login&creation_time=${acp_creation}&form_token=${acp_token}&credential=$(echo -n "${user}" | md5sum | cut -d' ' -f1)&redirect=index.php" \
        -L --max-redirs 5)
    if echo "$acp_response" | grep -qi "general\|admin.*index\|acp\|administration"; then
        echo "ACP access granted for ${user}"
        return 0
    else
        echo "ACP login failed for ${user}"
        return 1
    fi
}

phpbb_check_endpoint() {
    local url="$1" path="$2"
    local http_code
    http_code=$(curl -s -o /dev/null -w '%{http_code}' "${url}${path}")
    echo "GET ${path} -> HTTP ${http_code}"
    [ "$http_code" = "200" ]
}
