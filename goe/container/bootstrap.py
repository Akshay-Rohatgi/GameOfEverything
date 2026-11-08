"""Central registry for container bootstrap configurations.

Thin shim over ``goe.distros``: the base package set and the apt bootstrap
command now live on each ``DistroProfile`` so they stay consistent across
ProgressiveEnvironment (L2 testing) and TopologyEnvironment (L3 testing) and
vary correctly per OS.
"""

from goe.distros import APT_BASE_PACKAGES, get_profile

# Back-compat alias — the package set is defined on the distro profiles now.
UBUNTU_BASE_PACKAGES = list(APT_BASE_PACKAGES)


def get_bootstrap_command(target_type: str = "ubuntu") -> str:
    """Get the command to bootstrap a target container for the given OS.

    Args:
        target_type: Canonical distro name or alias (e.g. "ubuntu", "debian",
            "ubuntu_22_04"). Resolved via the distro profile registry.

    Returns:
        Bash command string to install base packages.
    """
    return get_profile(target_type).bootstrap_command()