"""Tests for the services layer — service registry and deployment scripts."""

from goe.models.system import ServiceSpec
from goe.services import get_registry


def test_registry_loads_recipes():
    """Test that the registry loads all service recipes."""
    registry = get_registry()
    available = registry.available_services()

    # Should have at least these core services
    assert "mysql" in available
    assert "openssh" in available
    assert "redis" in available
    assert "nginx" in available


def test_mysql_recipe_has_readiness_gate():
    """Test that the MySQL recipe includes a readiness wait loop."""
    registry = get_registry()
    recipe = registry.get_recipe("mysql")

    start_script = recipe["start"]

    # Must have a readiness loop
    assert "for i in $(seq" in start_script
    assert "mysqladmin ping" in start_script
    assert "FATAL" in start_script  # explicit failure if not ready


def test_openssh_recipe_has_config_vars():
    """Test that OpenSSH recipe has configurable parameters."""
    registry = get_registry()
    recipe = registry.get_recipe("openssh")

    config_vars = recipe.get("config_vars", {})
    assert "permit_root_login" in config_vars
    assert "password_auth" in config_vars


def test_deploy_all_generates_script():
    """Test that deploy_all generates a bash script."""
    registry = get_registry()
    specs = [
        ServiceSpec(id="mysql", config={"root_password": "toor"}),
        ServiceSpec(id="openssh", config={"permit_root_login": "yes"}),
    ]

    script = registry.deploy_all(specs)

    # Should be a bash script
    assert script.startswith("#!/bin/bash")

    # Should contain both services
    assert "mysql" in script
    assert "openssh" in script

    # Should apply config vars
    assert "toor" in script
    assert "PermitRootLogin yes" in script or "permit_root_login" in script.lower()

    # Should have readiness gates
    assert "Waiting for" in script


def test_deploy_all_empty_list():
    """Test that deploy_all handles empty service list."""
    registry = get_registry()
    script = registry.deploy_all([])
    assert script == ""


def test_restart_all_only_restarts_services_with_flag():
    """Test that restart_all only restarts services with restart_after_snapshot=true."""
    registry = get_registry()
    specs = [
        ServiceSpec(id="mysql"),  # has restart_after_snapshot: true
        ServiceSpec(id="openssh"),  # has restart_after_snapshot: true
    ]

    script = registry.restart_all(specs)

    # Should contain restart logic
    assert "#!/bin/bash" in script
    assert "mysqld_safe" in script  # from MySQL start script
    assert "sshd" in script  # from OpenSSH start script


def test_backward_compat_string_services():
    """Test that System.services accepts bare strings for backward compatibility."""
    from goe.models.system import System, NetworkConfig

    system = System(
        id="web",
        os="ubuntu_22_04",
        services=["mysql", "openssh"],  # old format
        network=NetworkConfig(hostname="web", exposed_ports=[80], internal_ports=[]),
    )

    # Should be coerced to ServiceSpec
    assert len(system.services) == 2
    assert all(isinstance(s, ServiceSpec) for s in system.services)
    assert system.services[0].id == "mysql"
    assert system.services[1].id == "openssh"
    assert system.services[0].config == {}


def test_service_spec_with_config():
    """Test ServiceSpec with config dict."""
    from goe.models.system import System, NetworkConfig

    system = System(
        id="db",
        os="ubuntu_22_04",
        services=[
            ServiceSpec(id="mysql", config={"root_password": "hunter2"}),
            ServiceSpec(id="redis"),
        ],
        network=NetworkConfig(hostname="db", exposed_ports=[3306], internal_ports=[]),
    )

    assert system.services[0].id == "mysql"
    assert system.services[0].config["root_password"] == "hunter2"
    assert system.services[1].id == "redis"
    assert system.services[1].config == {}
