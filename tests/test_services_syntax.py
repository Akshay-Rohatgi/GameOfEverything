"""Syntax validation tests for service recipes.

These tests verify that all recipes have valid structure and required fields
without needing Docker.
"""

import pytest
from pathlib import Path
import yaml

from goe.services import get_registry


def test_all_recipes_have_required_fields():
    """Test that all recipes have the minimum required fields."""
    registry = get_registry()
    required_fields = ["id", "description", "install", "start", "healthcheck", "ports"]

    for service_id in registry.available_services():
        recipe = registry.get_recipe(service_id)

        for field in required_fields:
            assert field in recipe, f"{service_id} missing required field: {field}"

        # Validate types
        assert isinstance(recipe["id"], str)
        assert isinstance(recipe["description"], str)
        assert isinstance(recipe["ports"], list)
        assert all(isinstance(p, int) for p in recipe["ports"])


def test_all_start_scripts_have_readiness_gates():
    """Test that all start scripts include readiness wait loops."""
    registry = get_registry()

    for service_id in registry.available_services():
        recipe = registry.get_recipe(service_id)
        start_script = recipe["start"]

        # Should have a wait loop
        assert "for i in" in start_script or "while" in start_script, \
            f"{service_id} start script missing readiness wait loop"

        # Should have FATAL error handling
        assert "FATAL" in start_script or "exit 1" in start_script, \
            f"{service_id} start script missing failure handling"


def test_all_config_vars_have_apply_scripts():
    """Test that all config_vars have apply scripts."""
    registry = get_registry()

    for service_id in registry.available_services():
        recipe = registry.get_recipe(service_id)
        config_vars = recipe.get("config_vars", {})

        for var_name, var_def in config_vars.items():
            assert "apply" in var_def, \
                f"{service_id} config var '{var_name}' missing 'apply' script"
            assert "{value}" in var_def["apply"], \
                f"{service_id} config var '{var_name}' apply script missing {{value}} placeholder"


def test_restart_after_snapshot_flag_exists():
    """Test that all recipes declare restart_after_snapshot."""
    registry = get_registry()

    for service_id in registry.available_services():
        recipe = registry.get_recipe(service_id)
        assert "restart_after_snapshot" in recipe, \
            f"{service_id} missing restart_after_snapshot flag"
        assert isinstance(recipe["restart_after_snapshot"], bool)


def test_no_set_e_in_service_scripts():
    """Test that service scripts don't use 'set -e' (they need custom error handling)."""
    registry = get_registry()

    for service_id in registry.available_services():
        recipe = registry.get_recipe(service_id)

        # Check all script sections
        for section in ["install", "configure", "start"]:
            script = recipe.get(section, "")
            # We specifically don't want 'set -e' in individual service scripts
            # because we need custom error handling per service
            # The combined deployment script has 'set -e' at the top, but services don't
            if "set -e" in script:
                pytest.fail(f"{service_id} {section} script has 'set -e' - use explicit error handling instead")


def test_healthcheck_matches_start_script():
    """Test that healthcheck command is consistent with start script validation."""
    registry = get_registry()

    for service_id in registry.available_services():
        recipe = registry.get_recipe(service_id)
        start_script = recipe["start"]
        healthcheck = recipe["healthcheck"]

        # If start script uses a command to check readiness, healthcheck should use similar command
        # This is a heuristic check
        if "mysqladmin ping" in start_script:
            assert "mysqladmin ping" in healthcheck, \
                f"{service_id} start uses mysqladmin but healthcheck doesn't"

        if "redis-cli ping" in start_script:
            assert "redis-cli" in healthcheck, \
                f"{service_id} start uses redis-cli but healthcheck doesn't"

        if "nc -z" in start_script:
            assert "nc -z" in healthcheck, \
                f"{service_id} start uses nc but healthcheck doesn't"


def test_yaml_recipes_are_valid():
    """Test that all YAML recipe files can be parsed."""
    recipes_dir = Path(__file__).parent.parent / "goe" / "services" / "recipes"

    for yaml_file in recipes_dir.glob("*.yaml"):
        with open(yaml_file) as f:
            try:
                data = yaml.safe_load(f)
                assert data is not None, f"{yaml_file.name} parsed to None"
                assert "id" in data, f"{yaml_file.name} missing 'id' field"
            except yaml.YAMLError as e:
                pytest.fail(f"{yaml_file.name} has invalid YAML: {e}")


def test_recipe_id_matches_filename():
    """Test that recipe ID matches the YAML filename."""
    recipes_dir = Path(__file__).parent.parent / "goe" / "services" / "recipes"

    for yaml_file in recipes_dir.glob("*.yaml"):
        with open(yaml_file) as f:
            data = yaml.safe_load(f)
            expected_id = yaml_file.stem
            actual_id = data["id"]
            assert actual_id == expected_id, \
                f"{yaml_file.name}: id '{actual_id}' should match filename '{expected_id}'"


def test_no_duplicate_service_ids():
    """Test that there are no duplicate service IDs."""
    registry = get_registry()
    services = registry.available_services()

    # Check for duplicates
    assert len(services) == len(set(services)), \
        f"Duplicate service IDs found: {services}"


def test_port_ranges_are_valid():
    """Test that all port numbers are in valid range."""
    registry = get_registry()

    for service_id in registry.available_services():
        recipe = registry.get_recipe(service_id)
        ports = recipe["ports"]

        for port in ports:
            assert 1 <= port <= 65535, \
                f"{service_id} has invalid port: {port}"
