"""Single-entity build pipeline — construction crew → deploy → L2 test → retry."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goe.models.entity import Entity
    from goe.models.report import BuildOutcome
    from goe.construction_crew.architect import ArchitectPlan


def _generate_file_tree(plan: "ArchitectPlan", runtime: str, app_dir: str | None = None) -> str:
    """Generate a tree representation of the planned file structure."""
    lines = []

    # Start with app directory
    if runtime == "system":
        lines.append("[dim]/opt/[/dim]")
        lines.append("└── setup.sh")
    else:
        display_dir = app_dir or "/opt/webapp"
        lines.append(f"[dim]{display_dir}/[/dim]")

        items = []

        # Main app file
        if runtime == "express":
            items.append("app.js")
        elif runtime == "flask":
            items.append("app.py")
        elif runtime in ("apache_php", "php"):
            items.append("index.php")

        # Endpoints as routes/pages
        if plan.endpoints:
            endpoint_files = set()
            for ep in plan.endpoints:
                # Extract file from path
                path = ep.path.lstrip('/')
                if '/' in path:
                    endpoint_files.add(path.split('/')[0])
                elif path:
                    endpoint_files.add(path)

            if len(endpoint_files) > 1:
                items.append("views/")

        # Database
        if plan.data_model.tables:
            if runtime in ("express", "flask"):
                items.append("app.db [dim](sqlite3)[/dim]")
            else:
                items.append("[dim](MySQL database)[/dim]")

        # Package files
        if runtime == "express":
            items.append("package.json")
        elif runtime == "flask":
            items.append("requirements.txt")

        # Build tree
        for i, item in enumerate(items):
            is_last = i == len(items) - 1
            prefix = "└──" if is_last else "├──"
            lines.append(f"{prefix} {item}")

            # If it's the views directory, show nested files
            if item == "views/" and plan.endpoints:
                endpoint_files = set()
                for ep in plan.endpoints:
                    path = ep.path.lstrip('/')
                    if '/' in path:
                        endpoint_files.add(path.split('/')[0])
                    elif path:
                        endpoint_files.add(path)

                nested_prefix = "    " if is_last else "│   "
                sorted_files = sorted(list(endpoint_files)[:3])
                for j, f in enumerate(sorted_files):
                    ext = ".ejs" if runtime == "express" else ".html"
                    is_last_file = j == len(sorted_files) - 1 and len(endpoint_files) <= 3
                    file_prefix = "└──" if is_last_file else "├──"
                    lines.append(f"{nested_prefix}{file_prefix} {f}{ext}")

                if len(endpoint_files) > 3:
                    lines.append(f"{nested_prefix}└── [dim]... {len(endpoint_files) - 3} more[/dim]")

    return "\n".join(lines)


def _lint_summary(findings) -> str:
    """One-line-per-finding summary of distro lint findings, for diagnosis text."""
    return "; ".join(f"[{f.where}] {f.token}: {f.message}" for f in findings)


def _apply_distro_lint(result, deploy_script, procedure, os):
    """Enforce distro-correctness on an otherwise-passing build.

    The agents are told the target distro, but that is advisory — this is the deterministic
    backstop. If the generated deploy script / procedure carries Ubuntu-isms that are wrong
    for ``os`` (e.g. ``systemctl`` on a non-systemd container, a canonical package name the
    distro renames), flip an otherwise-PASSING result to failed so the retry loop regenerates
    it with a precise, deterministic diagnosis. A build that already failed keeps its own
    diagnosis (the lint is only the *sole* reason to fail here), so we never mask a real bug.

    Returns (result, findings, forced) where ``forced`` is True only when the lint is what
    turned a passing build into a failing one.
    """
    from goe.construction_crew import distro_lint
    from goe.distros import get_profile

    try:
        profile = get_profile(os)
    except Exception:
        return result, [], False  # unknown os: do not assert a distro

    findings = distro_lint.lint(profile, deploy_script, procedure)
    forced = bool(findings) and result.passed
    if forced:
        from goe.executor.runner import ProcedureResult
        result = ProcedureResult(passed=False, error="distro lint: " + _lint_summary(findings))
    return result, findings, forced


def _fixup_served_ssh_keys(script: str, provided_values: dict | None) -> str:
    """Deterministically re-write any SSH private key this entity serves.

    The producer embeds the key as base64 and decodes it to a file (per the developer
    prompt). The LLM keeps getting the fragile parts wrong — mangled PEM bytes
    ("Load key: invalid format") or wrong permissions (644 where 600 was intended).
    Here we append, at the END of the deploy script, a deterministic re-write of the
    exact key material to the same path the script decoded it to, with the permissions
    the script intended (detected `chmod`, default 600). Running last, it overrides any
    earlier mangling. No-op when the entity serves no SSH key or no path is found.
    """
    if not script or not provided_values:
        return script

    import re

    # Collect the base64 key material for every SSH-key edge this entity provides.
    materials: list[str] = []
    for params in provided_values.values():
        if not isinstance(params, dict):
            continue
        ct = str(params.get("cred_type") or "").lower()
        secret = params.get("secret")
        if secret and "ssh" in ct and "key" in ct:
            materials.append(secret)
    if not materials:
        return script

    appended: list[str] = []
    single = len(materials) == 1

    def _detect_paths(b64: str) -> list[str]:
        paths: list[str] = []
        # Lines that both carry this key's inline base64 and redirect to a file.
        if b64 in script:
            for line in script.splitlines():
                if b64 in line and ">" in line and ("base64" in line):
                    m = re.search(r">>?\s*([^\s;&|>]+)", line)
                    if m:
                        paths.append(m.group(1).strip("\"'"))
        # Single-key fallbacks: the developer often does NOT embed the material inline —
        # it writes the key from an (uninjected) env var, e.g.
        #   printf '%s' "${FOO_KEY_B64}" | base64 -d > /root/.ssh/id_rsa_x
        # which leaves a correctly-permissioned but EMPTY file. Anchor to any
        # base64-decode redirect, or to a write targeting a conventional SSH key path.
        # Safe only when a single key is in play (no cross-assignment risk).
        if single:
            for m in re.finditer(r"base64\s+(?:-d|--decode)[^\n>]*>>?\s*([^\s;&|>]+)", script):
                paths.append(m.group(1).strip("\"'"))
            for m in re.finditer(
                r">>?\s*((?:/[^\s;&|>]*)?id_(?:rsa|ed25519|ecdsa|dsa)[^\s;&|>]*)", script
            ):
                paths.append(m.group(1).strip("\"'"))
        # Never write private material to a public-key path.
        return [p for p in dict.fromkeys(paths) if not p.endswith(".pub")]

    for b64 in materials:
        for path in _detect_paths(b64):
            cm = re.search(rf"chmod\s+(\d{{3,4}})\s+{re.escape(path)}\b", script)
            perms = cm.group(1) if cm else "600"
            appended.append(
                f"# [harness] deterministic SSH key write — exact PEM bytes + {perms} perms\n"
                f'mkdir -p "$(dirname "{path}")"\n'
                f"printf '%s' '{b64}' | base64 -d > {path}\n"
                f"chmod {perms} {path}"
            )

    if not appended:
        return script
    return script.rstrip() + "\n\n" + "\n".join(appended) + "\n"


def _l2_verify_port(runtime, artifact, registry):
    """Port the L2 build-verify should target: where the app ACTUALLY bound.

    Prefer ``artifact.port`` (the developer's real ``app.run(port=...)``) over the
    runtime template default. The developer sometimes binds a different port than the
    template specifies (e.g. ``:80`` where ``flask.yaml`` says ``:5000``); the verify
    curls ``${target_port}``, so targeting the template port would miss a healthy app
    (HTTP 000 / status=None) and fail the build on a phantom. Returns ``None`` for
    ``system`` entities (no web server) and falls back to the template port when the
    entity reports no port of its own.
    """
    if runtime == "system":
        return None
    return getattr(artifact, "port", None) or registry.port_for(runtime)


def _sanitize_ssh_dir_perms(script: str) -> str:
    """Force any ``.ssh`` directory to sshd-safe ``0700``, overriding an LLM loosen.

    sshd StrictModes unconditionally refuses a ``~/.ssh`` that is group- or
    world-writable ("Authentication refused: bad ownership or modes for directory"),
    so a key-based login silently fails with ``Permission denied (publickey)``.
    Developers building a Redis-BGSAVE key-injection entity routinely
    ``chmod 777 /root/.ssh`` so a root-running Redis can drop the RDB there — which is
    both unnecessary (root writes a ``0700`` dir it owns) and fatal (sshd then rejects
    the injected key). Append, at the END of the deploy script, a deterministic
    ``chmod 700`` of every ``.ssh`` directory the script touches, running after any
    earlier loosen. Safe: ``0700`` is the mode sshd requires, so it can never break a
    valid login, and a root writer can still write into it at attack time. No-op when
    the script references no ``.ssh`` directory path.
    """
    if not script:
        return script

    import re

    # Absolute ``.../.ssh`` directory paths referenced anywhere in the script.
    dirs: list[str] = []
    for m in re.finditer(r"(/[^\s;&|>'\"]*/\.ssh)(?=[\s;&|>'\"/]|$)", script):
        dirs.append(m.group(1))
    dirs = list(dict.fromkeys(dirs))
    if not dirs:
        return script

    appended = [
        "# [harness] sshd StrictModes: a group/world-writable ~/.ssh is rejected; "
        "force 0700 (root can still write into it)."
    ]
    for d in dirs:
        appended.append(f'[ -d "{d}" ] && chmod 700 "{d}"')
    return script.rstrip() + "\n\n" + "\n".join(appended) + "\n"


def _sanitize_world_readable_traversal(script: str) -> str:
    """Make the parent dirs of a deliberately world-readable file traversable.

    A "leak" entity (bash_history_leak, exposed-config, etc.) ``chmod 644`` a file so
    an unprivileged attacker can read it — but a world-readable file is unreachable if
    any ancestor directory lacks the ``o+x`` traverse bit. The LLM reliably sets the
    FILE mode and forgets the DIRECTORY mode: e.g. ``useradd -m deploy`` leaves
    ``/home/deploy`` at ``0700``, so ``nobody`` cannot ``cd`` into it to read the
    ``0644`` ``.bash_history`` (observed P2 live failure, build/system/ubuntu/
    design_flaw — the retry loop could not self-fix it). For every path the script
    makes world-readable, append a deterministic ``chmod o+x`` of each ancestor
    directory (statically known — the paths are literals), so traversal matches the
    intended read access.

    Scope guards: only paths the script makes world-readable (``o+r``) are considered,
    so private material left at ``0600`` is never exposed; ``/`` is never chmod-ed; and
    any ``.ssh`` ancestor is skipped so this never re-loosens a dir that
    ``_sanitize_ssh_dir_perms`` just forced to ``0700`` (sshd StrictModes). No-op when
    nothing is made world-readable.
    """
    if not script:
        return script

    import re

    def _world_readable(mode: str) -> bool:
        # Numeric mode (3- or 4-digit octal): the "other" bits are the last digit.
        if mode.isdigit():
            return bool(int(mode[-1]) & 4)
        # Symbolic mode granting read to other/all: o+r, a+r, o=...r, a=...r, bare +r.
        return bool(re.search(r"(?:^|,)\s*[oa]?[+=][rwxXst]*r", mode))

    files: list[str] = []
    # `chmod <mode> <path>` where <mode> grants world read. Stop the path at the first
    # shell metacharacter so trailing `&&`, `;`, redirects etc. are not captured.
    for m in re.finditer(r"\bchmod\s+(\S+)\s+(/[^\s;&|>'\"]+)", script):
        mode, path = m.group(1), m.group(2).strip("\"'")
        if _world_readable(mode):
            files.append(path)
    if not files:
        return script

    # Collect every ancestor directory of each world-readable path (excluding `/` and
    # any `.ssh` dir), preserving first-seen order.
    dirs: list[str] = []
    for f in files:
        parts = f.rstrip("/").split("/")  # ['', 'home', 'deploy', '.bash_history']
        for i in range(len(parts) - 1, 1, -1):  # parents only, skip the leaf
            d = "/".join(parts[:i])
            if not d or d == "/" or d.rsplit("/", 1)[-1] == ".ssh":
                continue
            if d not in dirs:
                dirs.append(d)
    if not dirs:
        return script

    appended = [
        "# [harness] a world-readable file is unreachable unless every parent dir is "
        "traversable; add o+x so the intended reader can reach it."
    ]
    for d in dirs:
        appended.append(f'[ -d "{d}" ] && chmod o+x "{d}"')
    return script.rstrip() + "\n\n" + "\n".join(appended) + "\n"


def _sanitize_env_source_printenv(procedure):
    """Rewrite the ``source <file> && printenv VAR`` idiom so a sourced ``KEY=VALUE`` file's
    variables actually reach the process environment that ``printenv`` reads.

    ``/etc/environment`` (and similar files) is plain ``KEY=VALUE`` with no ``export``, so
    ``source``-ing it sets *shell* variables that ``printenv`` — and any child process —
    never sees. The observed P2 ``env_vars_leak`` live failure was exactly this: the
    attacker step ``source /etc/environment && printenv DB_PASSWORD`` printed nothing. On
    top of that, ``source`` is a bashism that fails under ``/bin/sh``.

    The fix wraps the source in ``set -a`` / ``set +a`` (allexport) and uses POSIX ``.``,
    so every assignment the file makes is exported and readable, under any shell. Only a
    command that BOTH sources a file AND reads it back via ``printenv`` is touched — an
    ordinary ``source`` (profile scripts that already ``export``) is left alone. This is a
    deterministic procedure-side sanitizer, the attacker-command parallel to the three
    deploy-script sanitizers above; it rewrites at run time without mutating the stored
    procedure (so the no-op guards and diagnostician still see the crew's own output).
    """
    if procedure is None:
        return procedure

    import re

    # A `source <file>` / `. <file>` token: a leading delimiter (so `./x` and `..` are not
    # matched — `.` must be followed by whitespace then a path), the verb, and the path up
    # to the next shell metachar. Rewritten to an allexport dot-source.
    _src = re.compile(r"(?P<lead>^|[\s;&|(\"'])(?:source|\.)[ \t]+(?P<file>[^\s;&|)\"']+)")

    def _rewrite(cmd: str) -> str:
        if not cmd or "printenv" not in cmd:
            return cmd
        if not _src.search(cmd):
            return cmd
        return _src.sub(lambda m: f"{m.group('lead')}set -a; . {m.group('file')}; set +a", cmd)

    steps = getattr(procedure, "procedure", None) or []
    new_steps = []
    changed = False
    for step in steps:
        action = getattr(step, "action", None)
        cmd = getattr(action, "command", None)
        if isinstance(cmd, str):
            new_cmd = _rewrite(cmd)
            if new_cmd != cmd:
                try:
                    new_action = action.model_copy(update={"command": new_cmd})
                    new_steps.append(step.model_copy(update={"action": new_action}))
                    changed = True
                    continue
                except Exception:
                    pass  # fall through: keep the step as-is if the copy fails
        new_steps.append(step)

    if not changed:
        return procedure
    try:
        return procedure.model_copy(update={"procedure": new_steps})
    except Exception:
        return procedure


def _procedure_unchanged(old, new) -> bool:
    """True when a retry produced the same procedure it started with — a ``procedure_bug``
    fix that changed nothing, so re-testing it would fail identically."""
    try:
        return old is not None and new is not None and old.model_dump() == new.model_dump()
    except Exception:
        return False


def _artifact_unchanged(old, new) -> bool:
    """True when a retry produced the same build artifact it started with — an
    ``implementation_bug``/``design_flaw`` fix that regenerated a byte-identical app, so
    re-deploying and re-testing it would fail identically. Mirror of
    :func:`_procedure_unchanged` for the app side of the crew."""
    try:
        return old is not None and new is not None and old.model_dump() == new.model_dump()
    except Exception:
        return False


def _regenerated_artifact_unchanged(category, old_crew, new_crew) -> bool:
    """True when a regen left the artifact that ``category`` is responsible for changing
    byte-identical — a guaranteed no-op that will reproduce the same failure on re-test.

    This generalises the old procedure-only no-op guard across every category: a
    ``procedure_bug`` must change the *procedure*; an ``implementation_bug`` or
    ``design_flaw`` must change the *app artifact*. Comparing the wrong-for-the-category
    artifact would miss the no-op (e.g. an implementation_bug that re-emits the same app
    but a cosmetically different procedure)."""
    from goe.retry.diagnostician import DiagnosisCategory

    if category == DiagnosisCategory.procedure_bug:
        return _procedure_unchanged(
            getattr(old_crew, "procedure", None), getattr(new_crew, "procedure", None)
        )
    return _artifact_unchanged(
        getattr(old_crew, "artifact", None), getattr(new_crew, "artifact", None)
    )


def _step_ids(procedure) -> list[str]:
    """Ordered step ids declared in a procedure (empty list if none)."""
    steps = getattr(procedure, "procedure", None) or []
    return [sid for sid in (getattr(s, "step_id", None) for s in steps) if sid]


def _changed_step_ids(old_proc, new_proc) -> set[str]:
    """Step ids whose definition differs between two procedures — added, removed, or an
    action/expect/outputs change. Used to tell *which* steps a procedure fix actually
    touched, not merely whether anything changed."""
    def _by_id(p) -> dict:
        out: dict = {}
        for s in (getattr(p, "procedure", None) or []):
            sid = getattr(s, "step_id", None)
            if not sid:
                continue
            try:
                out[sid] = s.model_dump()
            except Exception:
                out[sid] = None
        return out

    old, new = _by_id(old_proc), _by_id(new_proc)
    return {sid for sid in set(old) | set(new) if old.get(sid) != new.get(sid)}


def _cited_step_ids(text: str, procedure) -> list[str]:
    """Step ids that exist in ``procedure`` and are named (case-insensitively, as whole
    words) in ``text`` — the steps a diagnosis explicitly points its fix at."""
    import re

    ids = _step_ids(procedure)
    if not ids or not text:
        return []
    low = text.lower()
    return [sid for sid in ids if re.search(rf"\b{re.escape(sid.lower())}\b", low)]


def _cited_steps_left_unchanged(diagnosis, result, old_proc, new_proc) -> list[str]:
    """Step ids the diagnosis blames (named in its text, or the failed step) that a
    procedure fix left byte-identical while still existing in the new procedure.

    These are fixes that regenerated *some* step but not the one the diagnosis said was
    wrong — re-testing them reproduces the same failure. Returned so the caller can
    re-prompt with a targeted "you must change step N" instead of burning a test cycle.
    """
    cited = set(_cited_step_ids(
        (getattr(diagnosis, "description", "") or "") + " "
        + (getattr(diagnosis, "evidence", "") or ""),
        old_proc,
    ))
    failed = getattr(result, "failed_step", None)
    if failed:
        cited.add(failed)
    if not cited:
        return []
    changed = _changed_step_ids(old_proc, new_proc)
    new_ids = set(_step_ids(new_proc))
    return [sid for sid in cited if sid in new_ids and sid not in changed]


# Diagnosis free-text that describes fixing the attacker's request/assertion — i.e. a
# verify-step defect that ``fix_procedure`` resolves — even when the LLM labelled it an
# implementation_bug. Kept tight so a genuine app defect (a 500, a missing column) is not
# mis-routed away from the developer.
_VERIFY_STEP_FIX_SIGNALS = (
    "assertion",
    "the expect",
    "expected assertion",
    "wrong url",
    "wrong path",
    "wrong endpoint",
    "wrong payload",
    "wrong request",
    "wrong method",
    "fix the procedure",
    "fix the attacker",
    "re-run the attacker",
    "rerun the attacker",
    "attack procedure is wrong",
    "attacker step",
    "procedure step",
)


def _should_reroute_to_procedure(diagnosis, result, procedure) -> bool:
    """True when an ``implementation_bug`` diagnosis actually describes a verify-step
    defect (the attacker's request or assertion is wrong, not the app), so it should
    route to ``fix_procedure`` rather than regenerate the developer artifact.

    Conservative by construction: fires only when the diagnosis BOTH names a concrete,
    existing procedure step (or the run's failed step) AND its text uses explicit
    procedure/assertion-fix language. A status/exit-code mismatch the app alone can cause
    is never re-routed unless the diagnosis itself blames the step. Even a wrong guess is
    self-correcting — the generalised no-op guard escalates a no-op procedure fix straight
    back to implementation_bug."""
    from goe.retry.diagnostician import DiagnosisCategory

    if getattr(diagnosis, "category", None) != DiagnosisCategory.implementation_bug:
        return False
    text = ((getattr(diagnosis, "description", "") or "") + " "
            + (getattr(diagnosis, "evidence", "") or "")).lower()
    if not any(sig in text for sig in _VERIFY_STEP_FIX_SIGNALS):
        return False
    names_step = bool(_cited_step_ids(text, procedure)) or bool(
        getattr(result, "failed_step", None)
    )
    return names_step


def build_entity(
    entity: "Entity",
    incoming_edges: dict | None = None,
    scope: str = "",
    verbose: bool = True,
    env=None,  # TestEnvironment | ProgressiveEnvironment | None
    edge_schemas: dict | None = None,
    system_context: str | None = None,
    provided_values: dict | None = None,
    console=None,  # RunConsole | None
    os: str = "ubuntu",
    peers: list | None = None,  # [(hostname_alias, peer_container), ...] to attach
) -> "BuildOutcome":
    """Run the full build pipeline for a single entity.

    Steps:
      1. Construction crew: Architect → Developer → Attacker
      2. Runtime template → deploy script
      3. Spin up TestEnvironment (or use provided env), deploy app
      4. Run L2 procedure executor
      5. On failure: diagnose → retry (escalation ladder)
      6. Teardown environment (if we own it)

    Args:
        entity: The entity spec to build.
        incoming_edges: Concrete values for incoming edges.
        scope: Docker container name prefix (avoid collisions during parallel builds).
        verbose: Print progress to stdout.
        env: Optional pre-created environment (ProgressiveEnvironment or TestEnvironment).
            If provided, build_entity will not call setup() or teardown().
        edge_schemas: Optional {edge_id: {"type", "direction", "params"}} for the entity's
            provided/required edges — constrains the param keys the developer may emit.
        system_context: Optional rendered markdown describing this entity's system, the
            platform-provided services, and sibling entities — injected into the architect and
            developer prompts so each entity builds only its own link.
        provided_values: Optional {edge_id: {param: concrete}} of already-determined values
            for edges this entity provides (resolved hosts, materialized secrets) that the
            developer must embed verbatim instead of regenerating.

    Returns:
        BuildOutcome wrapping the EntityResult plus the final deploy script,
        procedure, and outgoing edge values (the latter three empty on failure).
    """
    from goe.construction_crew.orchestrator import build as crew_build, CrewResult
    from goe.container.environment import TestEnvironment
    from goe.executor.runner import run as run_procedure
    from goe.models.report import BuildOutcome, EntityResult, EntityStatus
    from goe.retry.diagnostician import diagnose
    from goe.retry.router import retry as retry_crew
    from goe.runtimes.registry import get_registry

    incoming_edges = incoming_edges or {}
    runtime = entity.runtime.value
    registry = get_registry()

    def log(msg: str) -> None:
        if verbose:
            print(f"[build_entity:{entity.id}] {msg}")

    def section(title: str) -> None:
        if verbose:
            print(f"\n{'='*60}\n  {title}\n{'='*60}")

    def dump(label: str, content: str, max_lines: int = 60) -> None:
        if not verbose:
            return
        lines = content.splitlines()
        truncated = len(lines) > max_lines
        shown = "\n".join(lines[:max_lines])
        print(f"\n--- {label} ---\n{shown}")
        if truncated:
            print(f"... ({len(lines) - max_lines} more lines truncated)")

    # Phase 1 — Construction crew
    if console:
        console.crew_phase("Construction Crew")
        console.crew_agent_start("Architect", "designing attack plan")
    else:
        section("PHASE 1: Construction Crew")
        log("Running architect...")

    t0 = time.time()
    crew: CrewResult = crew_build(
        entity, incoming_edges, edge_schemas=edge_schemas,
        system_context=system_context, provided_values=provided_values,
    )
    duration = time.time() - t0

    if console:
        console.crew_agent_done("Architect + Developer + Attacker", duration)
        file_tree = _generate_file_tree(crew.plan, entity.runtime.value, getattr(crew.artifact, "app_dir", None))
        console.crew_plan_summary(
            crew.plan.runtime,
            crew.plan.summary,
            crew.plan.attack_entry_point,
            file_tree=file_tree
        )
    else:
        log(f"Crew finished in {duration:.1f}s")
        section("Architect Plan")
        log(f"Runtime:    {crew.plan.runtime}")
        log(f"Summary:    {crew.plan.summary}")
        log(f"Entry point: {crew.plan.attack_entry_point}")
        log(f"Success indicator: {crew.plan.success_indicator}")
        log(f"Vulnerability: {crew.plan.vulnerability_placement}")

    # Show source code snippets (first file only, truncated)
    if console and crew.artifact.source_files:
        first_file = list(crew.artifact.source_files.keys())[0]
        content = crew.artifact.source_files[first_file]
        lines = content.splitlines()[:15]  # First 15 lines
        console.crew_source_snippet(first_file, lines)
        if len(crew.artifact.source_files) > 1:
            console._line(f"  [dim]... and {len(crew.artifact.source_files) - 1} more file(s)[/dim]")
    elif not console:
        section("Generated Source Files")
        for fname, content in crew.artifact.source_files.items():
            dump(fname, content)
        if crew.artifact.db_setup:
            dump("schema.sql", crew.artifact.db_setup.schema_sql)
            dump("seed.sql", crew.artifact.db_setup.seed_sql)

    # Show procedure summary
    if console:
        import yaml as _yaml
        proc_dict = crew.procedure.model_dump()
        steps = proc_dict.get('procedure', [])
        console.crew_procedure_summary(steps)
    else:
        section("Generated Attack Procedure")
        import yaml as _yaml
        _proc_yaml = _yaml.dump(crew.procedure.model_dump(), default_flow_style=False)
        dump("procedure.yaml", _proc_yaml)

    # Persist to run-dir if artifact capture is active
    from goe.metrics import get_session
    _session = get_session()
    _art_run_dir = getattr(_session, "artifact_run_dir", None) if _session else None
    if _art_run_dir is not None:
        from goe.artifacts.writer import save_crew_artifacts
        save_crew_artifacts(entity.id, crew, _art_run_dir)
        log(f"Artifacts written to: {_art_run_dir}/entities/{entity.id}/")

    _owns_env = env is None
    if _owns_env:
        env = TestEnvironment(runtime=runtime, scope=scope or f"build_{entity.id[:16]}", os=os)
        env.setup()

    # Attach cross-host peers (e.g. the DB host a web tier connects to) onto this
    # entity's build network, so their hostnames resolve during the build exactly
    # as they will on the flat chain network at deploy time. Best-effort.
    if peers and hasattr(env, "attach_peer"):
        for alias, peer_container in peers:
            try:
                env.attach_peer(alias, peer_container)
            except Exception as e:  # never let peer wiring abort a build
                log(f"[peer] attach '{alias}' failed: {e}")

    def _make_deploy_script(artifact) -> str:
        if runtime == "system":
            script = artifact.source_files[artifact.primary_source]
            # Deterministically re-write any SSH key this entity serves (exact PEM
            # bytes + intended perms), overriding the LLM's fragile key handling.
            script = _fixup_served_ssh_keys(script, provided_values)
            # sshd rejects a group/world-writable ~/.ssh — force any such dir to 0700,
            # overriding a `chmod 777 /root/.ssh` the dev adds for a root-Redis BGSAVE
            # key injection (root writes a 0700 dir fine; 777 is what breaks the login).
            script = _sanitize_ssh_dir_perms(script)
            # A deliberately world-readable "leak" file (0644 .bash_history, config,
            # etc.) is unreachable if a parent dir lacks o+x — the LLM sets the file
            # mode but forgets the dir traverse bit (e.g. useradd -m leaves /home/deploy
            # at 0700). Add o+x to each ancestor so the intended reader can reach it.
            script = _sanitize_world_readable_traversal(script)
        else:
            script = registry.deploy(runtime, artifact)
        # Deterministically neutralize `systemctl` for no-systemd containers: the service
        # layer already owns start/restart, so a systemctl line is always wrong here and
        # otherwise loops the distro lint until the entity escalates to design_flaw.
        from goe.distros import get_profile
        return get_profile(os).sanitize_service_control(script)

    try:
        # Phase 2 — Deploy
        deploy_script = _make_deploy_script(crew.artifact)
        if console:
            console.crew_deploy_start()
        else:
            section("PHASE 2: Deploy")
            dump("deploy_script.sh", deploy_script)
            log("Deploying app...")

        t0_deploy = time.time()
        exit_code, stdout, stderr = env.deploy(deploy_script)
        deploy_duration = time.time() - t0_deploy

        if console:
            console.crew_deploy_result(exit_code == 0, deploy_duration)
        else:
            log(f"Deploy exit code: {exit_code}")
            if stdout.strip():
                dump("deploy stdout", stdout)
            if stderr.strip() and exit_code != 0:
                dump("deploy stderr", stderr)

        # Phase 3 — L2 test
        port = _l2_verify_port(runtime, crew.artifact, registry)
        ctx = {
            "target_host": env.get_target_host(),
            "attacker_host": env.get_attacker_host(),
            "target_port": str(port) if port else "",
            "edges": incoming_edges,
        }

        if console:
            console.crew_test_start(attempt=1)
        else:
            section("PHASE 3: L2 Procedure Execution")

        attempt = 0
        if exit_code != 0:
            # A broken/half-deployed container must NEVER yield a PASSED entity.
            # Skip run_procedure (its assertions could pass against stale state)
            # and route straight into the retry loop as a forced design_flaw.
            from goe.executor.runner import ProcedureResult
            log(f"Deploy script exited {exit_code} — forcing design_flaw retry")
            result = ProcedureResult(
                passed=False,
                error=f"deploy script exited {exit_code}: {stderr.strip() or '(no stderr)'}",
            )
            forced_deploy_failure = True
        else:
            result = run_procedure(_sanitize_env_source_printenv(crew.procedure), env, ctx)
            forced_deploy_failure = False

        # Distro lint — a clean build that still carries Ubuntu-isms for this `os` is
        # flipped to failed so the retry loop regenerates it (see _apply_distro_lint).
        result, lint_findings, forced_lint_failure = _apply_distro_lint(
            result, deploy_script, crew.procedure, os)
        if forced_lint_failure:
            log(f"Distro lint flagged {len(lint_findings)} issue(s): {_lint_summary(lint_findings)}")

        if console:
            # Show test steps with console UI
            passed_steps = sum(1 for s in result.steps if s.passed)
            for step in result.steps:
                console.crew_test_step(step.step_id, step.passed, step.reason)
            console.crew_test_result(result.passed, len(result.steps), passed_steps)
        else:
            log(f"L2 attempt {attempt + 1}: {'PASSED' if result.passed else 'FAILED'}")
            for step in result.steps:
                status = "PASS" if step.passed else "FAIL"
                log(f"  [{status}] {step.step_id}: {step.reason}")
                if not step.passed:
                    if step.raw.stdout:
                        dump(f"stdout ({step.step_id})", step.raw.stdout, max_lines=20)
                    if step.raw.stderr:
                        dump(f"stderr ({step.step_id})", step.raw.stderr, max_lines=10)
                    if step.raw.body:
                        dump(f"body ({step.step_id})", step.raw.body, max_lines=20)
                    if step.raw.error:
                        log(f"  error: {step.raw.error}")

        from goe.retry.diagnostician import Diagnosis, DiagnosisCategory
        diagnosis_history = []  # Track diagnosis categories to detect stuck loops
        while not result.passed:
            attempt += 1
            if forced_lint_failure:
                # Deterministic: the distro lint found an Ubuntu-ism wrong for this distro.
                # A deploy-script hit is an implementation_bug (regenerate dev + attacker);
                # a procedure-only hit is a procedure_bug (regenerate the attacker).
                deploy_hit = any(f.where == "deploy_script" for f in lint_findings)
                diagnosis = Diagnosis(
                    category=(DiagnosisCategory.implementation_bug if deploy_hit
                              else DiagnosisCategory.procedure_bug),
                    description="Distro lint: " + _lint_summary(lint_findings),
                    evidence=result.error or "",
                )
                forced_lint_failure = False
            elif forced_deploy_failure:
                # Deterministic: a non-zero deploy exit is a design_flaw by
                # definition — don't ask the LLM to second-guess a broken deploy.
                diagnosis = Diagnosis(
                    category=DiagnosisCategory.design_flaw,
                    description="Deploy script exited non-zero — app failed to deploy.",
                    evidence=result.error or "",
                )
                forced_deploy_failure = False
            else:
                if console:
                    console._line(f"\n[yellow]⚠[/yellow] Diagnosing failure...")
                else:
                    log(f"Diagnosing failure (attempt {attempt})...")
                diagnosis = diagnose(entity, crew.artifact, result, env, procedure=crew.procedure, ctx=ctx)

                # Route correction: a verify-step defect (the attacker's request/assertion
                # is wrong) that the diagnostician mislabelled implementation_bug should
                # fix the procedure, not regenerate the app. Scoped to LLM diagnoses — the
                # forced lint/deploy categories above are already deterministic.
                if _should_reroute_to_procedure(diagnosis, result, crew.procedure):
                    log(f"Re-routing implementation_bug → procedure_bug: diagnosis describes "
                        f"a verify-step fix. {diagnosis.description[:80]}")
                    diagnosis = Diagnosis(
                        category=DiagnosisCategory.procedure_bug,
                        description="Re-routed from implementation_bug — the attack procedure "
                                    "(request/assertion) is what needs fixing, not the app. "
                                    + diagnosis.description,
                        evidence=diagnosis.evidence,
                    )

            if console:
                console.crew_retry(diagnosis.category.value, attempt, 3)
                console._line(f"  [dim]{diagnosis.description[:100]}{'...' if len(diagnosis.description) > 100 else ''}[/dim]")
            else:
                log(f"Diagnosis: {diagnosis.category} — {diagnosis.description}")

            # Detect stuck retry loops: if same category 2x in a row, escalate to design_flaw
            if (len(diagnosis_history) >= 1
                and diagnosis_history[-1] == diagnosis.category
                and diagnosis.category != DiagnosisCategory.design_flaw):
                if console:
                    console._line(f"  [yellow]⚠[/yellow] [dim]Stuck retry loop detected (same category 2x) — escalating to design_flaw[/dim]")
                else:
                    log(f"Stuck retry loop detected: {diagnosis.category} → {diagnosis.category}. Escalating to design_flaw.")
                diagnosis = Diagnosis(
                    category=DiagnosisCategory.design_flaw,
                    description=f"Escalated from {diagnosis_history[-1].value} after 2 consecutive failures in same category. Original issue: {diagnosis.description}",
                    evidence=diagnosis.evidence,
                )

            diagnosis_history.append(diagnosis.category)

            def _regen(diag):
                return retry_crew(
                    entity, incoming_edges, crew, diag, attempt, edge_schemas=edge_schemas,
                    system_context=system_context, provided_values=provided_values,
                )

            def _max_retries_outcome(diag):
                log("Max retries exceeded.")
                return BuildOutcome(
                    result=EntityResult(
                        id=entity.id,
                        status=EntityStatus.FAILED,
                        attempts=attempt + 1,
                        failure_reason=f"{diag.category}: {diag.description}",
                        failure_category=diag.category.value,
                    ),
                )

            new_crew = _regen(diagnosis)
            if new_crew is None:
                return _max_retries_outcome(diagnosis)

            # Direction 1 — enforce the diagnosis→regen link at step granularity. A
            # procedure fix that rewrote *some* step but left the step(s) the diagnosis
            # blamed untouched will fail identically on those steps. Re-prompt once,
            # naming the steps that must change, before spending a test cycle. (A fix that
            # changed *nothing* is handled by the generalised no-op guard below.)
            if diagnosis.category == DiagnosisCategory.procedure_bug:
                stuck = _cited_steps_left_unchanged(
                    diagnosis, result, crew.procedure, new_crew.procedure)
                if stuck and not _procedure_unchanged(crew.procedure, new_crew.procedure):
                    log(f"fix_procedure changed other steps but left cited step(s) "
                        f"{', '.join(stuck)} unchanged — re-prompting with a targeted fix")
                    targeted = Diagnosis(
                        category=DiagnosisCategory.procedure_bug,
                        description=(f"You MUST modify step(s) {', '.join(stuck)} — the previous "
                                     f"fix left them unchanged, so the test failed identically. "
                                     + diagnosis.description),
                        evidence=diagnosis.evidence,
                    )
                    retried = _regen(targeted)
                    if retried is not None:  # best-effort; keep prior regen if ladder exhausted
                        new_crew = retried

            # Direction 2 — generalised no-op guard. If the regen left the artifact this
            # category is responsible for changing byte-identical, re-testing is guaranteed
            # to reproduce the failure. Escalate one rung (procedure_bug → implementation_bug
            # → design_flaw) and regenerate something that can actually change the outcome,
            # rather than burning a deploy/test cycle. One extra crew regen trades cheaply
            # against a wasted Docker cycle.
            _noop_escalation = {
                DiagnosisCategory.procedure_bug: DiagnosisCategory.implementation_bug,
                DiagnosisCategory.implementation_bug: DiagnosisCategory.design_flaw,
            }
            if _regenerated_artifact_unchanged(diagnosis.category, crew, new_crew):
                nxt = _noop_escalation.get(diagnosis.category)
                if nxt is not None:
                    log(f"{diagnosis.category.value} fix was a no-op (artifact unchanged) — "
                        f"escalating to {nxt.value}")
                    diagnosis = Diagnosis(
                        category=nxt,
                        description="Previous fix was a no-op (the regenerated artifact matched "
                                    "the original); escalating. " + diagnosis.description,
                        evidence=diagnosis.evidence,
                    )
                    if diagnosis_history:
                        diagnosis_history[-1] = diagnosis.category
                    new_crew = _regen(diagnosis)
                    if new_crew is None:
                        return _max_retries_outcome(diagnosis)
                else:
                    # design_flaw already regenerates the full crew — nowhere left to
                    # escalate. Proceed; the attempt ladder bounds further retries.
                    log("design_flaw full-crew regen was a no-op — no further escalation; "
                        "proceeding under the attempt ladder")

            crew = new_crew

            # Persist retry artifacts (updated app/procedure) and diff vs previous
            if _art_run_dir is not None:
                from goe.artifacts.writer import save_attempt_artifacts, write_attempt_diff
                save_attempt_artifacts(entity.id, attempt, crew, diagnosis, _art_run_dir)
                write_attempt_diff(entity.id, attempt, _art_run_dir)
                log(f"Attempt {attempt} artifacts written")

            # Always reset the attacker — clears detached background processes
            # (listeners, netcat, etc.) that survived from the previous attempt.
            log("Resetting attacker container...")
            env.reset_attacker()

            # Re-deploy if artifact changed (implementation_bug or design_flaw).
            # Also reset the target — old app processes, port bindings, and DB
            # files from the previous attempt would otherwise persist.
            if diagnosis.category != DiagnosisCategory.procedure_bug:
                log("Resetting target container...")
                env.reset_target()
                log("Re-deploying with updated artifact...")
                env.deploy(_make_deploy_script(new_crew.artifact))

            if console:
                console.crew_test_start(attempt=attempt + 1)
            result = run_procedure(_sanitize_env_source_printenv(crew.procedure), env, ctx)

            # Re-lint the regenerated crew so a retry can't slip an Ubuntu-ism through.
            result, lint_findings, forced_lint_failure = _apply_distro_lint(
                result, _make_deploy_script(crew.artifact), crew.procedure, os)
            if forced_lint_failure:
                log(f"Distro lint flagged {len(lint_findings)} issue(s) on retry: {_lint_summary(lint_findings)}")

            if console:
                passed_steps = sum(1 for s in result.steps if s.passed)
                for step in result.steps:
                    console.crew_test_step(step.step_id, step.passed, step.reason)
                console.crew_test_result(result.passed, len(result.steps), passed_steps)
            else:
                log(f"L2 attempt {attempt + 1}: {'PASSED' if result.passed else 'FAILED'}")

        return BuildOutcome(
            result=EntityResult(
                id=entity.id,
                status=EntityStatus.PASSED,
                attempts=attempt + 1,
            ),
            deploy_script=_make_deploy_script(crew.artifact),
            procedure=crew.procedure,
            outgoing_values=crew.outgoing_values,
        )

    finally:
        if _owns_env:
            env.teardown()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _main() -> None:
    import argparse
    import sys
    import yaml
    from goe.models.entity import Entity
    from goe.artifacts.run import artifact_run

    parser = argparse.ArgumentParser(description="Build a single GoE entity")
    parser.add_argument("--spec", required=True, help="Path to entity YAML spec")
    parser.add_argument("--scope", default="", help="Docker scope prefix")
    artifact_group = parser.add_mutually_exclusive_group()
    artifact_group.add_argument(
        "--artifacts",
        dest="artifacts",
        action="store_true",
        default=None,
        help="Save workflow artifacts (overrides goe.toml [artifacts].enabled)",
    )
    artifact_group.add_argument(
        "--no-artifacts",
        dest="artifacts",
        action="store_false",
        help="Disable artifact saving for this run",
    )
    args = parser.parse_args()

    with open(args.spec) as f:
        entity = Entity.model_validate(yaml.safe_load(f))

    command = " ".join(sys.argv)
    with artifact_run("build", command, capture=args.artifacts):
        outcome = build_entity(entity, scope=args.scope, verbose=True)

    result = outcome.result
    print(f"\nResult: {result.status} (attempts: {result.attempts})")
    if result.failure_reason:
        print(f"Failure: {result.failure_reason}")


if __name__ == "__main__":
    _main()
