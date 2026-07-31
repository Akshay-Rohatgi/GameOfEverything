"""Shared pytest fixtures for GoE v2 tests."""

import time
from pathlib import Path
import pytest
import yaml

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROCEDURES_DIR = FIXTURES_DIR / "procedures"
APPS_DIR = FIXTURES_DIR / "apps"


# ---------------------------------------------------------------------------
# Procedure YAML loading
# ---------------------------------------------------------------------------

def load_procedure(name: str):
    """Load a procedure YAML fixture by filename (without .yaml)."""
    from goe.models.procedure import Procedure
    path = PROCEDURES_DIR / f"{name}.yaml"
    with open(path) as f:
        data = yaml.safe_load(f)
    return Procedure.model_validate(data)


# ---------------------------------------------------------------------------
# App source helpers
# ---------------------------------------------------------------------------

def express_deploy_script(port: int = 3000) -> str:
    app_js = (APPS_DIR / "simple_express" / "app.js").read_text()
    return f"""
set -e
mkdir -p /app
cat > /app/app.js << 'GOE_APP_JS_EOF'
{app_js}
GOE_APP_JS_EOF

cd /app
npm init -y > /dev/null 2>&1
npm install express > /dev/null 2>&1
PORT={port} nohup node /app/app.js > /tmp/app.log 2>&1 &
sleep 2
curl -sf http://localhost:{port}/login > /dev/null && echo "App started OK"
"""


def flask_deploy_script(port: int = 5000) -> str:
    app_py = (APPS_DIR / "simple_flask" / "app.py").read_text()
    return f"""
set -e
mkdir -p /app
cat > /app/app.py << 'GOE_APP_PY_EOF'
{app_py}
GOE_APP_PY_EOF

pip3 install flask -q > /dev/null 2>&1
PORT={port} nohup python3 /app/app.py > /tmp/app.log 2>&1 &
sleep 2
curl -sf http://localhost:{port}/login > /dev/null && echo "App started OK"
"""


# ---------------------------------------------------------------------------
# Docker environment fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def express_env():
    """Session-scoped Express environment (one Docker setup for all express tests)."""
    from goe.container.environment import TestEnvironment
    env = TestEnvironment(runtime="express", scope="test_express")
    env.setup()
    script = express_deploy_script(port=3000)
    exit_code, stdout, stderr = env.deploy(script)
    assert exit_code == 0, f"Express deploy failed:\nstdout: {stdout}\nstderr: {stderr}"
    # Wait for app to be ready
    for _ in range(20):
        ok, _, _ = env._tool.exec_in_target("curl -sf http://localhost:3000/login > /dev/null && echo ok")
        if "ok" in ok or ok == 0:
            break
        time.sleep(1)
    yield env
    env.teardown()


@pytest.fixture(scope="session")
def express_env_ctx(express_env):
    """Interpolation context for Express tests."""
    target_host = express_env.get_target_host()
    attacker_host = express_env.get_attacker_host()
    return {
        "target_host": target_host,
        "attacker_host": attacker_host,
        "target_port": "3000",
        "edges": {},
    }


def ubuntu_deploy_script() -> str:
    """Deploy SSH + a low-priv user + a SUID rootbash to the ubuntu target."""
    return """
set -e
apt-get install -y --no-install-recommends openssh-server sshpass 2>/dev/null
mkdir -p /run/sshd
useradd -m -s /bin/bash lowpriv 2>/dev/null || true
echo 'lowpriv:password123' | chpasswd
sed -i 's/#PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
sed -i 's/#PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
/usr/sbin/sshd
cp /bin/bash /tmp/rootbash
chmod +s /tmp/rootbash
echo "ubuntu_env_ready"
"""


@pytest.fixture(scope="session")
def ubuntu_env():
    """Session-scoped Ubuntu environment with SSH + SUID privesc setup."""
    from goe.container.environment import TestEnvironment
    env = TestEnvironment(runtime="ubuntu", scope="test_ubuntu")
    env.setup()
    exit_code, stdout, stderr = env.deploy(ubuntu_deploy_script())
    assert exit_code == 0, f"Ubuntu deploy failed:\nstdout: {stdout}\nstderr: {stderr}"
    assert "ubuntu_env_ready" in stdout
    yield env
    env.teardown()


@pytest.fixture(scope="session")
def ubuntu_env_ctx(ubuntu_env):
    """Interpolation context for Ubuntu/SSH tests."""
    return {
        "target_host": ubuntu_env.get_target_host(),
        "attacker_host": ubuntu_env.get_attacker_host(),
        "target_port": "22",
        "edges": {},
    }
