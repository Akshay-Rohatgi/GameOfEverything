"""Docker integration tests for ProgressiveEnvironment service provisioning.

Verifies that provision() deploys services into the progressive container, that
services survive snapshot/restore cycles (restart_after_snapshot recipes get
restarted), and that reset_target() to the base snapshot restores services too.
"""

import pytest

from goe.container.progressive import ProgressiveEnvironment
from goe.models.system import NetworkConfig, ServiceSpec, System


def _system(services):
    return System(
        id="test_system",
        os="ubuntu_22_04",
        services=services,
        network=NetworkConfig(hostname="target", exposed_ports=[], internal_ports=[]),
    )


@pytest.mark.docker
def test_provision_deploys_services():
    """provision() should deploy services and leave them running in the target."""
    system = _system([ServiceSpec(id="mysql", config={"root_password": "rootpass1"})])
    penv = ProgressiveEnvironment(system_id=system.id, scope="test_provision")

    try:
        penv.setup()
        penv.provision(system)

        exit_code, stdout, _ = penv._exec_in_target("mysqladmin ping -h localhost --silent && echo PONG")
        assert exit_code == 0
        assert "PONG" in stdout

        assert penv.current_snapshot == "_base"
    finally:
        penv.teardown()


@pytest.mark.docker
def test_restore_restarts_services_after_snapshot():
    """Services that don't survive docker commit must be restarted on restore()."""
    system = _system([ServiceSpec(id="mysql", config={"root_password": "rootpass2"})])
    penv = ProgressiveEnvironment(system_id=system.id, scope="test_restore")

    try:
        penv.setup()
        penv.provision(system)

        # Snapshot an "entity" on top of the base-provisioned container.
        penv.snapshot("entity_1", "echo 'entity_1 deployed'")

        # Restore back to entity_1's snapshot — simulates a retry/fan-out reset.
        penv.restore("entity_1")

        exit_code, stdout, _ = penv._exec_in_target("mysqladmin ping -h localhost --silent && echo PONG")
        assert exit_code == 0, "MySQL should be restarted after restore"
        assert "PONG" in stdout

        # Password should still work — data persisted through docker commit.
        exit_code, _, _ = penv._exec_in_target("mysql -u root -prootpass2 -e 'SELECT 1;'")
        assert exit_code == 0
    finally:
        penv.teardown()


@pytest.mark.docker
def test_reset_target_to_base_restarts_services():
    """reset_target() with no entity snapshots yet should restore the base ('_base') state."""
    system = _system([ServiceSpec(id="redis", config={"requirepass": "redispass1"})])
    penv = ProgressiveEnvironment(system_id=system.id, scope="test_reset_base")

    try:
        penv.setup()
        penv.provision(system)

        # Simulate first entity's build corrupting state, then retry via reset_target().
        penv._exec_in_target("rm -f /etc/redis/redis.conf")
        penv.reset_target()

        exit_code, stdout, _ = penv._exec_in_target(
            "redis-cli --no-auth-warning -a redispass1 ping"
        )
        assert exit_code == 0
        assert "PONG" in stdout
    finally:
        penv.teardown()


# --- Unit tests (no Docker): attacker->target DNS invariant -----------------
# Regression for the cross-host build-verify blocker: the attacker reaches the
# target by its container --hostname, so get_target_host() MUST equal the
# hostname the build container runs under (self._hostname). Returning a literal
# "target" broke verify (HTTP 000 / status=None) once the build container began
# running under the system's real network hostname.

def test_get_target_host_matches_configured_hostname():
    """get_target_host() returns the real hostname the target runs under."""
    penv = ProgressiveEnvironment(system_id="s", scope="t", hostname="webhost")
    assert penv.get_target_host() == "webhost"


def test_get_target_host_defaults_to_target():
    """With no hostname supplied, the historical default 'target' is preserved."""
    penv = ProgressiveEnvironment(system_id="s", scope="t")
    assert penv.get_target_host() == "target"
