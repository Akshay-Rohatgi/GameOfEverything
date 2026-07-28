"""Integration tests for service deployment scripts.

These tests actually run the generated scripts in Docker containers to verify:
1. Services start successfully
2. Readiness checks work
3. Config vars are applied
4. Restart scripts work after snapshot
"""

import docker
import pytest
import time

from goe.models.system import ServiceSpec
from goe.services import get_registry


@pytest.fixture
def docker_client():
    """Docker client for integration tests."""
    return docker.from_env()


def _exec_in_container(container, command: str) -> tuple[int, str]:
    """Execute a command in a container and return (exit_code, output)."""
    result = container.exec_run(["bash", "-c", command])
    return result.exit_code, result.output.decode() if result.output else ""


@pytest.mark.docker
def test_mysql_deployment_in_container(docker_client):
    """Test that MySQL actually starts with the generated script."""
    registry = get_registry()
    specs = [ServiceSpec(id="mysql", config={"root_password": "testpass123"})]
    script = registry.deploy_all(specs)

    # Start ubuntu container
    container = docker_client.containers.run(
        "ubuntu:22.04",
        command="sleep infinity",
        detach=True,
        remove=False,
    )

    try:
        # Execute the deployment script
        exit_code, output = _exec_in_container(container, script)
        print(f"Deploy exit code: {exit_code}")
        print(f"Deploy output:\n{output}")

        # Should succeed
        assert exit_code == 0, f"Deploy failed: {output}"
        assert "MySQL is ready" in output or "All services deployed" in output

        # Verify MySQL is actually running
        exit_code, output = _exec_in_container(
            container, "mysqladmin ping -h localhost --silent && echo PONG"
        )
        assert exit_code == 0
        assert "PONG" in output

        # Verify password was set (try to connect with password)
        exit_code, output = _exec_in_container(
            container, "mysql -u root -ptestpass123 -e 'SELECT 1;'"
        )
        assert exit_code == 0

    finally:
        container.stop()
        container.remove()


@pytest.mark.docker
def test_openssh_deployment_in_container(docker_client):
    """Test that OpenSSH actually starts with the generated script."""
    registry = get_registry()
    specs = [
        ServiceSpec(id="openssh", config={"permit_root_login": "yes"}),
    ]
    script = registry.deploy_all(specs)

    container = docker_client.containers.run(
        "ubuntu:22.04",
        command="sleep infinity",
        detach=True,
        remove=False,
    )

    try:
        # Execute the deployment script
        exit_code, output = _exec_in_container(container, script)
        print(f"Deploy output:\n{output}")

        assert exit_code == 0, f"Deploy failed: {output}"
        assert "SSH is ready" in output or "All services deployed" in output

        # Verify SSH is listening
        exit_code, output = _exec_in_container(
            container, "nc -z localhost 22 && echo LISTENING"
        )
        assert exit_code == 0
        assert "LISTENING" in output

        # Verify config was applied
        exit_code, output = _exec_in_container(
            container, "grep 'PermitRootLogin yes' /etc/ssh/sshd_config"
        )
        assert exit_code == 0

    finally:
        container.stop()
        container.remove()


@pytest.mark.docker
def test_redis_deployment_in_container(docker_client):
    """Test that Redis actually starts with the generated script."""
    registry = get_registry()
    specs = [ServiceSpec(id="redis", config={"requirepass": "redis123"})]
    script = registry.deploy_all(specs)

    container = docker_client.containers.run(
        "ubuntu:22.04",
        command="sleep infinity",
        detach=True,
        remove=False,
    )

    try:
        exit_code, output = _exec_in_container(container, script)
        print(f"Deploy output:\n{output}")

        assert exit_code == 0, f"Deploy failed: {output}"
        assert "Redis is ready" in output or "All services deployed" in output

        # Verify Redis is responding
        exit_code, output = _exec_in_container(
            container, "redis-cli -a redis123 ping"
        )
        assert exit_code == 0
        assert "PONG" in output

    finally:
        container.stop()
        container.remove()


@pytest.mark.docker
def test_multi_service_deployment(docker_client):
    """Test deploying multiple services in one container."""
    registry = get_registry()
    specs = [
        ServiceSpec(id="mysql", config={"root_password": "mysql123"}),
        ServiceSpec(id="redis"),
    ]
    script = registry.deploy_all(specs)

    container = docker_client.containers.run(
        "ubuntu:22.04",
        command="sleep infinity",
        detach=True,
        remove=False,
    )

    try:
        exit_code, output = _exec_in_container(container, script)
        print(f"Deploy output:\n{output}")

        assert exit_code == 0, f"Deploy failed: {output}"
        assert "All services deployed successfully" in output

        # Verify both services are running
        exit_code, _ = _exec_in_container(container, "mysqladmin ping")
        assert exit_code == 0, "MySQL not running"

        exit_code, output = _exec_in_container(container, "redis-cli ping")
        assert exit_code == 0, "Redis not running"
        assert "PONG" in output

    finally:
        container.stop()
        container.remove()


@pytest.mark.docker
def test_restart_script_after_commit(docker_client):
    """Test that restart script works after docker commit (simulates snapshot restore)."""
    registry = get_registry()
    specs = [ServiceSpec(id="mysql", config={"root_password": "test123"})]

    deploy_script = registry.deploy_all(specs)
    restart_script = registry.restart_all(specs)

    # Step 1: Deploy MySQL in a container
    container = docker_client.containers.run(
        "ubuntu:22.04",
        command="sleep infinity",
        detach=True,
        remove=False,
    )

    try:
        # Deploy
        exit_code, output = _exec_in_container(container, deploy_script)
        assert exit_code == 0, f"Initial deploy failed: {output}"

        # Verify running
        exit_code, _ = _exec_in_container(container, "mysqladmin ping")
        assert exit_code == 0

        # Step 2: Commit the container (simulates snapshot)
        image = container.commit(repository="test-mysql-snapshot", tag="test")
        print(f"Committed snapshot: {image.id}")

        # Step 3: Stop and remove the container
        container.stop()
        container.remove()

        # Step 4: Start a new container from the snapshot
        restored_container = docker_client.containers.run(
            image.id,
            command="sleep infinity",
            detach=True,
            remove=False,
        )

        try:
            # MySQL should NOT be running (processes don't survive commit)
            exit_code, _ = _exec_in_container(restored_container, "mysqladmin ping")
            assert exit_code != 0, "MySQL should not be running after commit"

            # Step 5: Run the restart script
            exit_code, output = _exec_in_container(restored_container, restart_script)
            print(f"Restart output:\n{output}")
            assert exit_code == 0, f"Restart failed: {output}"

            # Step 6: Verify MySQL is running again
            exit_code, _ = _exec_in_container(restored_container, "mysqladmin ping")
            assert exit_code == 0, "MySQL should be running after restart"

            # Verify data persisted (password should still work)
            exit_code, _ = _exec_in_container(
                restored_container, "mysql -u root -ptest123 -e 'SELECT 1;'"
            )
            assert exit_code == 0, "Password should still work after restart"

        finally:
            restored_container.stop()
            restored_container.remove()
            image.remove(force=True)

    finally:
        # Cleanup original container if it still exists
        try:
            container.remove(force=True)
        except:
            pass


@pytest.mark.docker
def test_nginx_deployment_in_container(docker_client):
    """Test that Nginx actually starts with the generated script."""
    registry = get_registry()
    specs = [ServiceSpec(id="nginx")]
    script = registry.deploy_all(specs)

    container = docker_client.containers.run(
        "ubuntu:22.04",
        command="sleep infinity",
        detach=True,
        remove=False,
    )

    try:
        exit_code, output = _exec_in_container(container, script)
        print(f"Deploy output:\n{output}")

        assert exit_code == 0, f"Deploy failed: {output}"
        assert "Nginx is ready" in output or "All services deployed" in output

        # Verify Nginx is serving
        exit_code, output = _exec_in_container(
            container, "curl -sf http://localhost/"
        )
        assert exit_code == 0, "Nginx not serving"

    finally:
        container.stop()
        container.remove()


@pytest.mark.docker
def test_samba_deployment_in_container(docker_client):
    """Test that Samba actually starts with the generated script."""
    registry = get_registry()
    specs = [ServiceSpec(id="samba")]
    script = registry.deploy_all(specs)

    container = docker_client.containers.run(
        "ubuntu:22.04",
        command="sleep infinity",
        detach=True,
        remove=False,
    )

    try:
        exit_code, output = _exec_in_container(container, script)
        print(f"Deploy output:\n{output}")

        assert exit_code == 0, f"Deploy failed: {output}"
        assert "Samba is ready" in output or "All services deployed" in output

        # Verify Samba is responding
        exit_code, output = _exec_in_container(
            container, "smbclient -L localhost -N"
        )
        assert exit_code == 0, "Samba not responding"

    finally:
        container.stop()
        container.remove()


@pytest.mark.docker
def test_ftp_deployment_in_container(docker_client):
    """Test that FTP actually starts with the generated script."""
    registry = get_registry()
    specs = [ServiceSpec(id="ftp")]
    script = registry.deploy_all(specs)

    container = docker_client.containers.run(
        "ubuntu:22.04",
        command="sleep infinity",
        detach=True,
        remove=False,
    )

    try:
        exit_code, output = _exec_in_container(container, script)
        print(f"Deploy output:\n{output}")

        assert exit_code == 0, f"Deploy failed: {output}"
        assert "FTP is ready" in output or "All services deployed" in output

        # Verify FTP port is listening
        exit_code, output = _exec_in_container(
            container, "nc -z localhost 21 && echo LISTENING"
        )
        assert exit_code == 0, "FTP not listening"
        assert "LISTENING" in output

    finally:
        container.stop()
        container.remove()


@pytest.mark.docker
def test_apache_deployment_in_container(docker_client):
    """Test that Apache actually starts with the generated script."""
    registry = get_registry()
    specs = [ServiceSpec(id="apache")]
    script = registry.deploy_all(specs)

    container = docker_client.containers.run(
        "ubuntu:22.04",
        command="sleep infinity",
        detach=True,
        remove=False,
    )

    try:
        exit_code, output = _exec_in_container(container, script)
        print(f"Deploy output:\n{output}")

        assert exit_code == 0, f"Deploy failed: {output}"
        assert "Apache is ready" in output or "All services deployed" in output

        # Verify Apache is serving
        exit_code, output = _exec_in_container(
            container, "curl -sf http://localhost/"
        )
        assert exit_code == 0, "Apache not serving"
        assert "Apache is running" in output

    finally:
        container.stop()
        container.remove()


@pytest.mark.docker
def test_service_failure_detection(docker_client):
    """Test that a service failure is properly detected and reported."""
    # Create a broken service script (typo in command)
    broken_script = """
#!/bin/bash
set -e
# This will fail
nonexistent_command_that_will_fail
"""

    container = docker_client.containers.run(
        "ubuntu:22.04",
        command="sleep infinity",
        detach=True,
        remove=False,
    )

    try:
        exit_code, output = _exec_in_container(container, broken_script)

        # Should fail with non-zero exit code
        assert exit_code != 0, "Broken script should fail"

    finally:
        container.stop()
        container.remove()
