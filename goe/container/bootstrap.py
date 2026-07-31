"""Central registry for container bootstrap configurations.

Defines standard package sets for target containers to ensure consistency
between ProgressiveEnvironment (L2 testing) and TopologyEnvironment (L3 testing).
"""

# Base packages for all ubuntu target containers
UBUNTU_BASE_PACKAGES = [
    # Core utilities
    "curl",
    "wget",
    "ca-certificates",
    "gnupg",
    "lsb-release",

    # Networking tools
    "iproute2",
    "net-tools",
    "iputils-ping",

    # Process management
    "procps",

    # Common attack surface packages (pre-installed on real servers)
    "sudo",
    "openssh-server",
    "sshpass",
]



def get_bootstrap_command(target_type: str = "ubuntu") -> str:
    """Get the apt-get command to bootstrap a target container.

    Args:
        target_type: Type of target ("ubuntu" for now, could expand)

    Returns:
        Bash command string to install base packages
    """
    if target_type == "ubuntu":
        packages = " ".join(UBUNTU_BASE_PACKAGES)
        return (
            "apt-get update -y && "
            "DEBIAN_FRONTEND=noninteractive apt-get install -y "
            f"{packages}"
        )

    raise ValueError(f"Unknown target type: {target_type}")


