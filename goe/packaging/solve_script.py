"""Deterministic transpiler: validated attack ``Procedure`` → standalone ``solve.sh``.

The GoE runner (``goe/executor/runner.py``) executes a ``Procedure`` step by step
inside a ``TopologyEnvironment``. That is not something a human or CI can run
directly. This module compiles the *same* validated ``Procedure`` into a
self-contained bash script that runs from the Kali attacker container, reproduces
the attack chain, and exits 0 (printing proof) when the attacker succeeds — the
CTF-standard "here is the solution, run it and watch it win."

Correctness rests on mirroring the runner exactly:

* ``${edge.<id>.<param>}`` → the literal concrete value, substituted verbatim just
  like ``interpolate()`` does. These are usually secrets living inside single
  quotes; inserting the literal keeps the emitted command byte-identical to what
  the runner executed.
* ``${system.<id>.host|port}`` and the ``${target_host|target_port|attacker_host}``
  builtins → shell variables declared at the top of the script (default = the
  Docker hostname / port), so the same script can be retargeted via env vars
  (e.g. ``SYSTEM_SSH_SERVER_HOST=1.2.3.4``) without editing it.
* ``${steps.<id>.<name>}`` → a shell variable ``SOLVE_<id>_<name>`` populated live
  from the producing step's ``outputs`` capture — exactly as the runner captured
  it at run time.

Because a passing L3 chain test proves the runner executed these commands to
success, the compiled script reproduces that success.

Only the action/assertion subset that chain procedures actually use is supported
(``exec_attacker``/``exec_attacker_bg``/``http_request``/``sleep``/``listen`` and
their shell/HTTP assertions). Browser actions and ``exec_target`` — which never
appear in a chain procedure — raise :class:`UnsupportedActionError` so the packager
can skip emitting a script rather than emit a broken one.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from goe.executor.interpolation import resolve_static_ctx
from goe.models.procedure import (
    AllAssertion,
    BodyContainsAssertion,
    BodyRegexAssertion,
    ExecAttackerAction,
    ExecAttackerBgAction,
    ExitCodeAssertion,
    HttpRequestAction,
    ListenAction,
    ReceivedContainsAssertion,
    ReceivedRegexAssertion,
    SleepAction,
    StatusAssertion,
    StdoutContainsAssertion,
    StdoutRegexAssertion,
)

if TYPE_CHECKING:
    from goe.graph.models import EntityGraph
    from goe.models.procedure import Assertion, Procedure, Step


class UnsupportedActionError(Exception):
    """Raised when a step uses an action/assertion not representable in bash.

    Chain procedures never use browser actions or ``exec_target``; this only trips
    on single-entity browser playbooks, which the packager handles by skipping the
    solve script (with a warning) instead of shipping something broken.
    """


_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def _shq(s: str) -> str:
    """Single-quote a string for safe inclusion in a bash command."""
    return "'" + s.replace("'", "'\\''") + "'"


def _ident(s: str) -> str:
    """Sanitise an id into a valid bash identifier fragment."""
    return re.sub(r"[^A-Za-z0-9_]", "_", s)


def _wrap(inner: str, state: str) -> str:
    """Emit a shell-variable reference (``${inner}``) valid in the given quote state.

    ``state`` is the bash quoting context at the point of substitution:
      * ``"normal"`` — unquoted: double-quote to avoid word-splitting/globbing.
      * ``"double"`` — inside "…": plain expansion.
      * ``"single"`` — inside '…': break out (``'"${x}"'``) since '…' suppresses it.
    """
    if state == "single":
        return "'\"${" + inner + "}\"'"
    if state == "double":
        return "${" + inner + "}"
    return "\"${" + inner + "}\""


# ---------------------------------------------------------------------------
# Interpolation → bash (quote-state-aware)
# ---------------------------------------------------------------------------

def _bashify(template: str, ctx: dict) -> str:
    """Rewrite a procedure template string into a bash-ready string.

    Walks the string tracking bash quote state so that ``${...}`` placeholders are
    replaced with a representation that expands correctly wherever they appear:

      * edge params  → literal concrete value (verbatim, like ``interpolate()``)
      * system/builtin → ``${SYSTEM_<ID>_HOST}`` etc. (env-overridable shell var)
      * steps        → ``${SOLVE_<id>_<name>:-}`` shell var
      * anything else → left as the literal ``${...}`` (mirrors interpolate())
    """
    systems = ctx.get("systems", {})
    edges = ctx.get("edges", {})
    builtins = ctx.get("builtins", {})

    out: list[str] = []
    state = "normal"
    i = 0
    n = len(template)
    while i < n:
        ch = template[i]

        # Placeholder — replace regardless of quote state; the state only decides
        # how we format the replacement so it expands as intended.
        if ch == "$" and i + 1 < n and template[i + 1] == "{":
            m = _VAR_RE.match(template, i)
            if m:
                out.append(_render_ref(m.group(1), state, systems, edges, builtins))
                i = m.end()
                continue

        # Track quoting of the literal (non-placeholder) characters.
        if state == "normal":
            if ch == "'":
                state = "single"
            elif ch == '"':
                state = "double"
            elif ch == "\\" and i + 1 < n:
                out.append(ch)
                out.append(template[i + 1])
                i += 2
                continue
        elif state == "single":
            if ch == "'":
                state = "normal"
        elif state == "double":
            if ch == '"':
                state = "normal"
            elif ch == "\\" and i + 1 < n:
                out.append(ch)
                out.append(template[i + 1])
                i += 2
                continue

        out.append(ch)
        i += 1

    return "".join(out)


def _render_ref(key: str, state: str, systems: dict, edges: dict, builtins: dict) -> str:
    parts = key.split(".")

    if parts[0] == "edge" and len(parts) == 3:
        # Literal secret/value, verbatim — identical to interpolate().
        val = edges.get(parts[1], {}).get(parts[2])
        return val if val is not None else "${" + key + "}"

    if parts[0] == "system" and len(parts) == 3 and parts[1] in systems:
        field = parts[2]
        if field in ("host", "port"):
            return _wrap(f"SYSTEM_{_ident(parts[1]).upper()}_{field.upper()}", state)
        return "${" + key + "}"

    if parts[0] == "steps" and len(parts) == 3:
        return _wrap(f"SOLVE_{_ident(parts[1])}_{_ident(parts[2])}:-", state)

    if len(parts) == 1 and parts[0] in builtins:
        return _wrap(parts[0].upper(), state)

    # Unknown reference — leave literal (mirrors interpolate leaving it unresolved).
    return "${" + key + "}"


# ---------------------------------------------------------------------------
# Assertions → bash tests
# ---------------------------------------------------------------------------

def _assertion_tests(assertion: "Assertion", step_id: str) -> list[tuple[str, str]]:
    """Return ``[(test_command, fail_arg), ...]``; all must pass.

    ``fail_arg`` is a fully bash-quoted argument for ``_fail`` (single-quoted for
    arbitrary needles; double-quoted where a runtime value like ``$rc`` should show
    through). Sources mirror ``goe/executor/assertions.py``: shell/received and
    ``stdout_*`` check ``$out``, ``body_*`` check ``$body``, ``status`` checks
    ``$status``, ``exit_code`` checks ``$rc``.
    """
    if isinstance(assertion, AllAssertion):
        tests: list[tuple[str, str]] = []
        for sub in assertion.all:
            tests += _assertion_tests(sub, step_id)
        return tests

    if isinstance(assertion, ExitCodeAssertion):
        return [(f'[ "$rc" -eq {assertion.exit_code} ]',
                 f'"{step_id}: expected exit_code {assertion.exit_code}, got $rc"')]

    if isinstance(assertion, StatusAssertion):
        return [(f'[ "$status" = "{assertion.status}" ]',
                 f'"{step_id}: expected status {assertion.status}, got $status"')]

    if isinstance(assertion, (StdoutContainsAssertion, ReceivedContainsAssertion)):
        needle = getattr(assertion, "stdout_contains", None) or assertion.received_contains
        return [(f'printf %s "$out" | grep -qF -- {_shq(needle)}',
                 _shq(f"{step_id}: stdout/received does not contain {needle!r}"))]

    if isinstance(assertion, (StdoutRegexAssertion, ReceivedRegexAssertion)):
        pat = getattr(assertion, "stdout_regex", None) or assertion.received_regex
        return [(f'printf %s "$out" | grep -qP -- {_shq(pat)}',
                 _shq(f"{step_id}: stdout/received does not match {pat!r}"))]

    if isinstance(assertion, BodyContainsAssertion):
        return [(f'printf %s "$body" | grep -qF -- {_shq(assertion.body_contains)}',
                 _shq(f"{step_id}: body does not contain {assertion.body_contains!r}"))]

    if isinstance(assertion, BodyRegexAssertion):
        return [(f'printf %s "$body" | grep -qP -- {_shq(assertion.body_regex)}',
                 _shq(f"{step_id}: body does not match {assertion.body_regex!r}"))]

    raise UnsupportedActionError(
        f"assertion {type(assertion).__name__} is not representable in bash"
    )


# ---------------------------------------------------------------------------
# Outputs → bash captures
# ---------------------------------------------------------------------------

def _capture_line(var: str, spec: str, src_var: str) -> str:
    """Emit ``<var>="$(...)"`` capturing per the output spec (mirrors outputs.py).

    ``src_var`` is ``out`` for shell/received/http-stdout, ``body`` for parsed body,
    ``_raw`` for HTTP header extraction.
    """
    spec = spec.strip()

    if spec in ("stdout", "body"):
        return f'{var}="${{{src_var}}}"'

    m = re.match(r'^regex\("(.+)"\)$', spec)
    if m:
        pat = m.group(1)
        # Pattern passed via env to keep it clear of the single-quoted perl program.
        return (
            f'{var}="$(printf %s "${{{src_var}}}" | GOE_PAT={_shq(pat)} '
            "perl -0777 -ne 'if(/$ENV{GOE_PAT}/){print defined $1 ? $1 : $&}')\""
        )

    m = re.match(r'^header\("(.+)"\)$', spec)
    if m:
        name = m.group(1)
        return (
            f'{var}="$(printf %s "$_raw" | grep -i {_shq("^" + name + ":")} '
            "| head -n1 | cut -d: -f2- | sed 's/^ *//' | tr -d '\\r')\""
        )

    m = re.match(r'^json\("(.+)"\)$', spec)
    if m:
        path = m.group(1)
        return (
            f'{var}="$(printf %s "$body" | python3 -c '
            "'import sys,json\\n"
            "try:\\n"
            "    d=json.load(sys.stdin)\\n"
            "    for k in sys.argv[1].strip(chr(46)).split(chr(46)):\\n"
            "        d=d[k]\\n"
            "    print(d)\\n"
            "except Exception:\\n"
            "    pass' "
            f"{_shq(path)})\""
        )

    # Unknown spec → capture full stdout as a safe default.
    return f'{var}="${{{src_var}}}"'


# ---------------------------------------------------------------------------
# Step emission
# ---------------------------------------------------------------------------

def _emit_step(step: "Step", ctx: dict) -> list[str]:
    lines: list[str] = [f'_step {_shq(step.step_id)}']
    action = step.action

    if isinstance(action, ExecAttackerAction):
        cmd = _bashify(action.command, ctx)
        lines.append(f'out="$({cmd})"; rc=$?')
        src = "out"

    elif isinstance(action, ExecAttackerBgAction):
        cmd = _bashify(action.command, ctx)
        lines.append(f'nohup bash -c {_shq(cmd)} >/dev/null 2>&1 &')
        lines.append("rc=0; out=''")
        src = "out"

    elif isinstance(action, HttpRequestAction):
        parts = ["curl", "-s", "-i", "-X", action.method.upper()]
        curl = " ".join(_shq(p) for p in parts)
        for k, v in action.headers.items():
            curl += f' -H "{_bashify(k, ctx)}: {_bashify(v, ctx)}"'
        if action.body is not None:
            curl += f' --data-binary "{_bashify(action.body, ctx)}"'
        curl += f' "{_bashify(action.url, ctx)}"'
        lines.append(f'_raw="$({curl})"; rc=$?')
        lines.append('status="$(printf %s "$_raw" | awk \'NR==1{print $2; exit}\')"')
        lines.append('body="$(printf %s "$_raw" | awk \'f{print} /^\\r?$/{f=1}\')"')
        lines.append('out="$_raw"')
        src = "out"

    elif isinstance(action, ListenAction):
        lines.append(
            f'out="$(timeout {action.duration} nc -lvnp {action.port} 2>&1)"; rc=$?'
        )
        src = "out"

    elif isinstance(action, SleepAction):
        lines.append(f'sleep {action.seconds}; rc=0; out=""')
        src = "out"

    else:
        raise UnsupportedActionError(
            f"action {type(action).__name__} is not representable in bash"
        )

    # Assertion (fail → print + exit 1).
    if step.expect is not None:
        for test, fail_arg in _assertion_tests(step.expect, step.step_id):
            lines.append(f'{test} || _fail {fail_arg}')

    # Output captures for downstream steps.
    for name, spec in step.outputs.items():
        var = f"SOLVE_{_ident(step.step_id)}_{_ident(name)}"
        cap_src = "body" if (spec.strip() == "body") else src
        lines.append(_capture_line(var, spec, cap_src))

    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compile_solve_script(graph: "EntityGraph", procedure: "Procedure") -> str:
    """Compile a validated ``Procedure`` into a standalone ``solve.sh`` string.

    Raises :class:`UnsupportedActionError` if any step uses an action or assertion
    that cannot be represented in bash (browser steps / ``exec_target``).
    """
    static = resolve_static_ctx(graph)
    systems = static["systems"]

    # Builtins for legacy single-entity procedures: default target host/port to the
    # sole system's endpoint when unambiguous.
    builtins: dict[str, str] = {"attacker_host": "attacker"}
    if len(graph.systems) == 1:
        only = graph.systems[0]
        builtins["target_host"] = only.network.hostname
        builtins["target_port"] = str(only.network.exposed_ports[0]) if only.network.exposed_ports else ""
    ctx = {"systems": systems, "edges": static["edges"], "builtins": builtins}

    lines: list[str] = [
        "#!/usr/bin/env bash",
        "# GoE solve script — auto-generated from the validated attack chain. Do not edit.",
        "# Run from the Kali attacker container on the scenario network:",
        "#   docker compose up -d && docker compose exec attacker bash /goe/solve.sh",
        "# Retarget a system by overriding its host/port env var, e.g.:",
        "#   SYSTEM_SSH_SERVER_HOST=1.2.3.4 bash solve.sh",
        "#",
        "# No `set -e`: each step handles its own failure, mirroring the GoE runner.",
        "",
        '_fail() { echo "[FAIL] $*" >&2; exit 1; }',
        '_step() { echo "[*] $*" >&2; }',
        "",
        "# --- system endpoints (override via env) ---",
    ]
    for sid, info in systems.items():
        up = _ident(sid).upper()
        lines.append(f'SYSTEM_{up}_HOST="${{SYSTEM_{up}_HOST:-{info.get("host", "")}}}"')
        lines.append(f'SYSTEM_{up}_PORT="${{SYSTEM_{up}_PORT:-{info.get("port", "")}}}"')
    if "target_host" in builtins:
        lines.append(f'TARGET_HOST="${{TARGET_HOST:-{builtins["target_host"]}}}"')
        lines.append(f'TARGET_PORT="${{TARGET_PORT:-{builtins["target_port"]}}}"')
    lines.append(f'ATTACKER_HOST="${{ATTACKER_HOST:-{builtins["attacker_host"]}}}"')
    lines.append("")

    for step in procedure.procedure:
        lines += _emit_step(step, ctx)

    lines.append('echo "[+] SUCCESS: attacker objective reached." >&2')
    lines.append("exit 0")
    lines.append("")
    return "\n".join(lines)
