---
id: install_package
description: Installs system packages non-interactively.
required_vars: [package_name]
---
# Atom: Install Package
Installs a plain OS package or tool a scenario needs (e.g. a CTF binary, a client
utility). Long-running **services** (databases, SSH, Samba, web servers) are owned by
the service-recipe layer instead — declare them with a `requires_service` atom so the
deterministic, distro-aware recipe installs and starts them; this atom is for the
remaining package installs that have no recipe.

### Logic Requirements:
1. Install packages non-interactively using the target distro's package manager — see the
   **Target OS** section for which manager to use and how to invoke it (on apt-based distros set
   `DEBIAN_FRONTEND=noninteractive` to avoid prompts).
2. Refresh the package index once before installing.
3. Install `<package_name>`, naming the package as it exists on the target distro.

### Testing Guidance:
1. Verify the package is installed using the target distro's own package query — see the
   **Target OS** section (e.g. `dpkg -l | grep <package_name>` on apt-based distros,
   `rpm -q <package_name>` on Red Hat family). Prefer confirming the delivered binary is on
   `PATH` (`command -v <binary>`), which is distro-neutral.
2. If the package provides a service, confirm it is running with a process check (e.g. `pgrep <service_name>` or `service <service_name> status`) — `systemctl` is NOT available in these containers.

### Synthesis Guidance:
The Builder should combine multiple package requests into a single install command to improve container build speed.