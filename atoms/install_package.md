---
id: install_package
description: Installs system packages non-interactively.
required_vars: [package_name]
---
# Atom: Install Package
Ensures the necessary services are present for the lab.

### Logic Requirements:
1. Set `DEBIAN_FRONTEND=noninteractive` to prevent hanging in Docker.
2. Run `apt-get update` once before installations.
3. Use `apt-get install -y <package_name>`.

### Testing Guidance:
1. Verify the package is installed: `dpkg -l | grep <package_name>`
2. Check the service status if applicable: `systemctl status <service_name>`

### Synthesis Guidance:
The Builder should combine multiple package requests into a single `apt-get install` command to improve container build speed.