"""Unit tests for SSH-key materialization, propagation, and service boundaries (no Docker/LLM).

Covers the two root-cause fixes for the SMB→SSH key scenario:
  - a single SSH keypair is generated once and shared across producer + consumer (base64), and
  - declared system services are installed by the ServiceRegistry, not by entity scripts.
"""

import base64
from pathlib import Path

from goe.graph.build_scheduler import BuildScheduler
from goe.graph.models import EntityGraph
from goe.graph.secrets import derive_public_key, materialize_secrets
from goe.flow.orchestrator import (
    _incomplete_consumed_edges,
    _provided_values_for,
    _system_chain_context,
)

FIXTURES = Path(__file__).parent / "fixtures" / "graphs"


def _graph() -> EntityGraph:
    return EntityGraph.from_yaml(FIXTURES / "valid_smb_ssh_keychain.yaml")


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------

def test_materialize_fills_ssh_secret_as_base64_pem():
    graph = _graph()
    edge = graph.edge_by_id("smb_to_ssh_key")
    assert edge.params["secret"].concrete is None

    filled = materialize_secrets(graph)

    assert "smb_to_ssh_key" in filled
    secret = edge.params["secret"].concrete
    assert secret is not None
    pem = base64.b64decode(secret).decode()
    assert pem.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    # cred_type pinned so the consumer knows to derive a public key
    assert edge.params["cred_type"].concrete == "ssh_key"


def test_materialize_is_idempotent():
    graph = _graph()
    materialize_secrets(graph)
    first = graph.edge_by_id("smb_to_ssh_key").params["secret"].concrete
    # Second pass must not regenerate an already-filled secret.
    again = materialize_secrets(graph)
    assert again == {}
    assert graph.edge_by_id("smb_to_ssh_key").params["secret"].concrete == first


def test_producer_and_consumer_share_one_keypair():
    """The private key the SMB entity serves must authorize on the SSH entity."""
    graph = _graph()
    materialize_secrets(graph)
    secret_b64 = graph.edge_by_id("smb_to_ssh_key").params["secret"].concrete

    # Producer writes this exact private key; consumer derives the public half from the SAME
    # bytes — so the derivation must succeed and yield a usable authorized_keys line.
    private_pem = base64.b64decode(secret_b64).decode()
    pub = derive_public_key(private_pem)
    assert pub.startswith("ssh-rsa ")


def test_non_ssh_creds_not_materialized():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    # sqli_to_ssh is a password creds_for edge — left for the producer to seed, not generated.
    filled = materialize_secrets(graph)
    assert filled == {}
    assert graph.edge_by_id("sqli_to_ssh").params["secret"].concrete is None


# ---------------------------------------------------------------------------
# Propagation: producer provided-values + consumer incoming
# ---------------------------------------------------------------------------

def test_provided_values_expose_materialized_secret_to_producer():
    graph = _graph()
    materialize_secrets(graph)
    smb = graph.entity_by_id("smb_key_share")
    provided = _provided_values_for(graph, smb)
    # The SMB producer is handed the materialized key so its script embeds the exact bytes.
    assert "secret" in provided["smb_to_ssh_key"]
    assert provided["smb_to_ssh_key"]["cred_type"] == "ssh_key"


def test_consumer_incoming_includes_materialized_secret_even_without_producer_emit():
    """The SSH consumer must receive the secret off the graph edge, not only via producer emit."""
    graph = _graph()
    materialize_secrets(graph)
    # Simulate resolve.py filling host on the creds edge.
    graph.edge_by_id("smb_to_ssh_key").params["host"].concrete = "sshserver"

    sched = BuildScheduler(graph)
    # Producer builds first; emit ONLY the username (secret is pre-materialized, not re-emitted).
    ent, _ = sched.next_buildable()
    assert ent.id == "smb_key_share"
    sched.report_complete("smb_key_share", {"smb_to_ssh_key": {"user": "admin"}})

    ent2, incoming = sched.next_buildable()
    assert ent2.id == "ssh_login"
    edge_in = incoming["smb_to_ssh_key"]
    assert edge_in["user"] == "admin"            # from producer emit
    assert edge_in["cred_type"] == "ssh_key"     # from graph edge (materialized)
    assert "secret" in edge_in and base64.b64decode(edge_in["secret"])  # materialized key bytes


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def test_path_like_ssh_secret_flagged_incomplete():
    graph = _graph()
    materialize_secrets(graph)
    # Regress the original bug: a file *path* instead of key material.
    graph.edge_by_id("smb_to_ssh_key").params["secret"].concrete = "/srv/samba/public/id_rsa"
    graph.edge_by_id("smb_to_ssh_key").params["user"].concrete = "admin"
    graph.edge_by_id("smb_to_ssh_key").params["host"].concrete = "sshserver"

    gaps = _incomplete_consumed_edges(graph, {"smb_key_share": object(), "ssh_login": object()})
    assert any("smb_to_ssh_key.secret" in g for g in gaps)


def test_materialized_secret_not_flagged_incomplete():
    graph = _graph()
    materialize_secrets(graph)
    for p in ("user", "host"):
        graph.edge_by_id("smb_to_ssh_key").params[p].concrete = "x"
    gaps = _incomplete_consumed_edges(graph, {"smb_key_share": object(), "ssh_login": object()})
    assert not any("smb_to_ssh_key.secret" in g for g in gaps)


# ---------------------------------------------------------------------------
# System & chain context
# ---------------------------------------------------------------------------

def test_system_chain_context_names_services_and_siblings():
    graph = _graph()
    ctx = _system_chain_context(graph, graph.entity_by_id("smb_key_share"))
    assert "smb_server" in ctx
    assert "smb" in ctx                       # the platform-provided service
    assert "ssh_login" in ctx                 # sibling entity
    assert "sshserver" in ctx                 # sibling's system hostname
    # Boundary instruction present
    assert "Build ONLY your own link" in ctx
