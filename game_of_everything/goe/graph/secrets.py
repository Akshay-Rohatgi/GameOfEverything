"""Build-time materialization of generable edge secrets (currently SSH keypairs).

Mirrors ``planner/resolve.py`` (which fills ``host``/``port`` deterministically) but runs
during the build *workflow* and fills secret *material* that cannot be a chosen literal.

The motivating case is an SSH keypair shared across two systems: an entity that serves the
private key (e.g. over an anonymous SMB share) and another entity that authorizes the matching
public key. Each system's deploy script runs in its own container, so if either script
generated the key with ``ssh-keygen`` at *deploy* time the two halves would never match. The
key must therefore be generated **once**, here, and embedded as a literal in *both* deploy
scripts.

Secrets are transported **base64-encoded** so a multi-line PEM survives the JSON/edge/LLM
channel intact. (The original failure wrote a file *path* into ``secret`` and the consumer
silently regenerated its own key.) Producers embed ``echo '<b64>' | base64 -d > keyfile`` and
consumers derive the public half with ``ssh-keygen -y`` — both reference the same literal.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

from goe.models.edge import EdgeType

if TYPE_CHECKING:
    from goe.graph.models import EntityGraph

SSH_KEY = "ssh_key"


def materialize_secrets(graph: "EntityGraph") -> dict[str, dict[str, str]]:
    """Fill generable ``secret`` params on the graph's edges deterministically.

    Mutates ``graph`` in place: for every ``creds_for`` edge whose credential is an SSH key
    and whose ``secret`` is not yet concrete, generate a keypair and store
    ``base64(private_pem)`` in ``secret.concrete`` (and pin ``cred_type.concrete`` to
    ``"ssh_key"``). Idempotent — already-filled secrets are left untouched, so it is safe to
    call again on resume.

    Returns ``{edge_id: {param: concrete}}`` describing what was filled (for logging/tests).
    """
    filled: dict[str, dict[str, str]] = {}
    for edge in graph.edges:
        if edge.type != EdgeType.creds_for:
            continue
        secret = edge.params.get("secret")
        cred_type = edge.params.get("cred_type")
        if secret is None or secret.concrete is not None:
            continue
        if not _is_ssh_key(cred_type):
            continue

        private_pem = generate_ssh_private_key()
        secret.concrete = base64.b64encode(private_pem.encode()).decode("ascii")
        payload = {"secret": secret.concrete}
        if cred_type is not None and cred_type.concrete is None:
            cred_type.concrete = SSH_KEY
            payload["cred_type"] = SSH_KEY
        filled[edge.id] = payload
    return filled


def _is_ssh_key(cred_type) -> bool:
    """True when a ``cred_type`` param denotes an SSH key (structural or concrete)."""
    if cred_type is None:
        return False
    value = (cred_type.concrete or cred_type.structural or "").lower()
    return "ssh" in value and "key" in value


# ---------------------------------------------------------------------------
# Crypto helpers (also used by tests to assert producer/consumer key agreement)
# ---------------------------------------------------------------------------

def generate_ssh_private_key(bits: int = 2048) -> str:
    """Generate an unencrypted RSA private key in OpenSSH PEM format.

    OpenSSH format (``-----BEGIN OPENSSH PRIVATE KEY-----``) is directly usable by both
    ``ssh -i`` and ``ssh-keygen -y`` inside the deploy/attack containers.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode("ascii")


def derive_public_key(private_pem: str) -> str:
    """Return the OpenSSH ``authorized_keys`` line for an OpenSSH-format private key."""
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_ssh_private_key(private_pem.encode(), password=None)
    pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    )
    return pub.decode("ascii")
