---
id: decoy_artifact_set
description: Places several near-identical artifacts where only one is genuine and the rest are convincing but inert, so the attacker's task is discrimination rather than access.
required_vars: [artifact_kind, real_index, decoy_count]
---
# Atom: Decoy Artifact Set
Access alone does not solve this step. The attacker reaches a set of similar artifacts and must work out which one matters, using evidence that lives elsewhere in the scenario.

### Logic Requirements:

1. Produce `decoy_count + 1` artifacts of `artifact_kind`; the one at `real_index` is genuine.
2. All of them MUST be reachable by the same method. A decoy the attacker never sees is not a decoy.
3. Decoys MUST be shaped like the answer — well-formed values of the same kind. A decoy that is obviously wrong teaches nothing.
4. Something in the scenario MUST identify which is genuine: a timestamp, a name, a log line, a document. Discrimination MUST be inference, never guessing.
5. Decoys MUST satisfy no edge. A downstream step fed a decoy value MUST fail closed.

### Common Patterns:

- Five recorded arrivals at an entry point; only one matches the person named in a separate report.
- Four backup archives; only the one predating the incident still holds the pre-rotation credential.
- Three similar service accounts; only one appears in the access log.
- Several API keys in a leaked bundle; only one is un-revoked, and the revocation list is elsewhere.

### Testing Guidance:

**Layer 1 (Internal):**
- Confirm all `decoy_count + 1` artifacts exist and are well-formed
- Confirm the discriminating evidence exists and is reachable on its own

**Layer 2 (External):**
- Assert every artifact is reachable — a decoy that 404s has silently reduced the difficulty and nothing else reports it
- Assert the genuine artifact satisfies its outgoing edge
- Assert every decoy does NOT: attempt the downstream step with each decoy value and require failure

The last check is the one that matters and the only one that can fail silently in the direction that hurts. A decoy that happens to work turns an inference step into a guessing game with several winning answers, and a suite that only asks whether the intended path works cannot see it.

### Synthesis Guidance:

Difficulty here moves in two independent directions. `decoy_count` raises the cost of a brute-force sweep; the subtlety of the discriminator raises the cost of reasoning. Raising only the first produces tedium, raising only the second produces a step that is either instant or impossible. Move both.

Where possible put the discriminating evidence on a different system from the artifacts, so the step is a correlation across the graph rather than a careful read of one page.

Composes with `jpeg_comment_steg` (the note names the index, the index lists the set) and with `policy_derived_credential` (retired accounts in a staff export are this atom applied to identities).
