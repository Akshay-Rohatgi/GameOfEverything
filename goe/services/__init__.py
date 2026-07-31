"""Service deployment layer — deterministic infrastructure recipes.

Services (MySQL, OpenSSH, Samba, etc.) are deployed with proven bash scripts
that include proper readiness gates and error handling. This replaces the
fragile LLM-generated service deployment.

Usage:
    from goe.services import get_registry

    registry = get_registry()
    script = registry.deploy_all([
        ServiceSpec(id="mysql", config={"root_password": "toor"}),
        ServiceSpec(id="openssh"),
    ])
"""

from goe.services.registry import ServiceRegistry, get_registry

__all__ = ["ServiceRegistry", "get_registry"]
