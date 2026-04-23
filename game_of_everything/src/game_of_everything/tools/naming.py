"""Central Docker container/network naming helpers.

All naming across test_environment, chain_test_environment, and
finalize_topology derives from these functions — single source of truth.

Convention:
  box container  → goe_{box_id}
  attacker       → goe_attacker              (or goe_{scope}_attacker for parallel isolation)
  network        → goe_net                   (or goe_{scope}_net for parallel isolation)

The optional ``scope`` parameter is used only during parallel per-box testing
to avoid Docker name collisions when multiple boxes run simultaneously.
End-users and generated output always see the un-scoped names.
"""


def box_container_name(box_id: str) -> str:
    """Return the canonical Docker container name for a target box."""
    return f"goe_{box_id}"


def attacker_container_name(scope: str = "") -> str:
    """Return the Docker container name for the attacker.

    ``scope`` should be set to ``box_id`` only during parallel per-box
    testing, to prevent collisions across simultaneous test environments.
    For chain testing and deployment, leave ``scope`` empty.
    """
    return f"goe_{scope}_attacker" if scope else "goe_attacker"


def network_name(scope: str = "") -> str:
    """Return the Docker bridge network name.

    ``scope`` should be set to ``box_id`` only during parallel per-box
    testing. For chain testing and deployment the network is ``goe_net``.
    """
    return f"goe_{scope}_net" if scope else "goe_net"
