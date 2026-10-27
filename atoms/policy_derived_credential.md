---
id: policy_derived_credential
description: Publishes a password-construction policy on an internal page so a valid credential can be derived from public rules plus a discoverable account name; the credential is never stored as a literal string anywhere on the system.
required_vars: [policy_path, rule_template, account_source_path, suffix]
---
# Atom: Policy-Derived Credential
An internal standard documents how staff passwords are built. A separate artifact identifies which account is live. Neither is a secret on its own, and the password exists nowhere on disk — the attacker synthesises it by combining the two.

### Logic Requirements:

1. A policy page at `policy_path` MUST state the construction rule as a compliance control would — numbered standard, rationale, effective date.
2. An account source at `account_source_path` MUST make exactly one live account identifiable, with retired entries present so that choosing correctly is a real step.
3. A service MUST accept the derived credential. HTTP Basic auth is sufficient.
4. The password MUST be set by applying `rule_template` at build time. It MUST NOT appear as a literal in any file, environment variable, comment or history — `grep -r` across the built image must not find it.

### Common Patterns:

- IT security standard, plus a staff CSV export, plus a departmental portal on Basic auth.
- Onboarding wiki naming the initial-password formula, plus a new-starters list.
- A rotation runbook naming the seasonal pattern, plus a ticket naming who rotated last.
- A vendor integration guide specifying the service-account convention, plus an exposed config listing the integrations in use.

### Testing Guidance:

**Layer 1 (Internal):**
- Confirm the password does not appear as a literal anywhere in the image
- Confirm the account source distinguishes live from retired accounts

**Layer 2 (External):**
- Fetch the policy page and assert it contains the rule
- Fetch the account source and assert it contains the live account
- Derive the credential in the procedure from those two fetched values, then authenticate
- Assert a wrong derivation is rejected — the retired account, or the rule without capitalisation — and that the rejection body carries nothing sensitive

A procedure that hardcodes the password proves the service authenticates. It does not prove the credential is derivable, and derivability is the whole lesson.

### Synthesis Guidance:

This atom consumes two knowledge sources and produces a real `creds_for` edge, which makes it a natural chain hinge: everything before it is reading, everything after it is access.

Its difficulty is entirely in noticing that the two documents relate. If they are linked from the same index, or the policy page names the account, the step collapses. Put them in different services or behind different earlier steps so that combining them is inference.

Do not also leak the finished password through `exposed_env_vars`, `bash_history_leak` or a config file — that shortcut makes the chain vestigial while every test still passes. If a leaked copy is wanted for realism, give it a stale value that no longer authenticates.
