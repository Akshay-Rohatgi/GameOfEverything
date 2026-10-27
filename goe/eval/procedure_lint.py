"""Static lint for attacker procedures: find assertions that can't discriminate.

The attacker agent writes the exploit and the assertions that grade it. Nothing
checks that those assertions can tell a working exploit from a broken one. An
`expect` of `status: 200`, or a `body_contains` that also matches the error page,
passes a procedure that proves nothing — and passes it green in CI forever.

The full answer is a dynamic negative control: run the procedure against a target
with the vulnerability absent and require failure. That needs the executor and a
baseline fixture. This module is the part that needs neither, so it runs under
`pytest -m 'not docker and not llm'`.

Reads Procedure YAML (goe/models/procedure.py) without importing pydantic, so it
can report on documents that fail strict validation too.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterator

import yaml

# Assertion keys whose value is content the exploit is meant to have produced.
TEXT_KEYS = frozenset({
    "stdout_contains", "received_contains", "body_contains", "url_contains",
    "url_equals", "evaluate_result_contains", "extracted_contains",
    "title_contains", "contains",
})
REGEX_KEYS = frozenset({
    "stdout_regex", "received_regex", "body_regex", "evaluate_result_regex",
    "extracted_regex",
})
# Keys that assert structure, not content. Fine on their own; just not proof of
# a value, so the generic/short/tautology rules don't apply to them.
STRUCTURAL_KEYS = frozenset({
    "status", "exit_code", "selector", "selector_visible",
    "selector_not_visible", "cookie_exists", "count", "name", "key",
})

# Action fields that carry what the step transmits.
SENT_KEYS = ("url", "body", "headers", "command", "script", "path", "fields",
             "filename", "file_content", "selector")

# So common that matching them proves nothing: an error page, a login form and a
# 404 body all routinely contain several.
GENERIC = frozenset({
    "ok", "error", "true", "false", "success", "failed", "failure", "login",
    "admin", "user", "users", "password", "welcome", "home", "index", "page",
    "server", "http", "html", "<html>", "null", "none", "data", "result",
    "status", "message", "root", "test", "flag",
})

MIN_LEN = 4
DYNAMIC = re.compile(r"\$\{\s*(edge|steps)\.")


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str  # "error" | "warn"
    step: str
    detail: str

    def line(self) -> str:
        return f"  {'ERROR' if self.severity == 'error' else 'warn '} " \
               f"[{self.rule}] {self.step}: {self.detail}"


def _walk_assertions(expect: Any) -> Iterator[dict]:
    """Yield leaf assertions, flattening `all:` nesting."""
    if not isinstance(expect, dict):
        return
    if "all" in expect:
        for sub in expect.get("all") or []:
            yield from _walk_assertions(sub)
        return
    yield expect


def _sent_blob(action: Any) -> str:
    if not isinstance(action, dict):
        return ""
    parts = []
    for k in SENT_KEYS:
        v = action.get(k)
        if v is not None:
            parts.append(v if isinstance(v, str) else json.dumps(v, default=str))
    return " ".join(parts).lower()


def lint_procedure(doc: Any) -> list[Finding]:
    out: list[Finding] = []

    steps = doc.get("procedure") if isinstance(doc, dict) else None
    if not isinstance(steps, list) or not steps:
        return [Finding("unparsed", "error", "<document>",
                        "no 'procedure' list found — an unreadable procedure is "
                        "not a clean one")]

    dynamic_seen = False
    content_proof_anywhere = False
    status_only_steps: list[tuple[str, list[int]]] = []
    declared_outputs: dict[str, str] = {}   # output name -> step it came from
    all_text = []

    for i, step in enumerate(steps):
        last = i == len(steps) - 1
        if not isinstance(step, dict):
            out.append(Finding("unparsed", "error", f"#{i}",
                               "step is not a mapping"))
            continue

        sid = str(step.get("step_id") or f"#{i}")
        action = step.get("action") or {}
        atype = action.get("type") if isinstance(action, dict) else None
        expect = step.get("expect")

        # exec_target is god-view only, per goe/models/procedure.py.
        if atype == "exec_target":
            out.append(Finding("exec_target_in_procedure", "error", sid,
                               "exec_target is L1-diagnostic only and must not "
                               "appear in an L2 attack procedure — it runs "
                               "inside the target, so it proves nothing about "
                               "what an attacker can reach"))

        for name in (step.get("outputs") or {}):
            declared_outputs[str(name)] = sid

        leaves = list(_walk_assertions(expect))
        if not leaves:
            out.append(Finding(
                "no_expect", "error" if last else "warn", sid,
                "no expect" + (" — final step, so the exploit's payoff is "
                               "unverified" if last else "")))
            continue

        texts: list[tuple[str, str]] = []   # (assertion key, value)
        structural_only = True
        for a in leaves:
            for k, v in a.items():
                if k in TEXT_KEYS and isinstance(v, str):
                    texts.append((k, v))
                    structural_only = False
                elif k in REGEX_KEYS:
                    structural_only = False
                elif k not in STRUCTURAL_KEYS:
                    structural_only = False

        all_text.extend(v for _, v in texts)
        if any(DYNAMIC.search(v) for _, v in texts):
            dynamic_seen = True
        if not structural_only:
            content_proof_anywhere = True

        # Success-status-only. Recorded per step but judged for the PROCEDURE:
        # a step that posts a payload and asserts 201, followed by a step that
        # verifies the payload fired, is a correct pattern. Only a procedure in
        # which NO step ever asserts content is proving nothing.
        statuses = [a["status"] for a in leaves
                    if isinstance(a.get("status"), int)]
        if structural_only and statuses and all(200 <= s < 400 for s in statuses) \
                and len(leaves) == len(statuses):
            status_only_steps.append((sid, statuses))

        sent = _sent_blob(action)
        for key, t in texts:
            from_stdout = key in ("stdout_contains", "received_contains")
            s = t.strip()
            if not s or DYNAMIC.search(s):
                continue
            low = s.lower()
            # Context matters: `stdout_contains: root` after `whoami` is a
            # strong assertion; `body_contains: root` on a web page is not.
            # Calibrated against tests/fixtures/procedures/*.yaml.
            if low in GENERIC and not from_stdout:
                out.append(Finding("generic_text", "warn", sid,
                                   f"asserts on {s!r}, which appears on error "
                                   "and login pages too"))
            elif len(s) < MIN_LEN:
                out.append(Finding("short_text", "warn", sid,
                                   f"asserts on {len(s)} characters ({s!r}) — "
                                   "likely to match incidentally"))
            if len(low) >= MIN_LEN and sent and low in sent:
                out.append(Finding(
                    "tautological", "warn", sid,
                    f"asserts on {s!r}, which this step transmits — check this is "
                    "a stored-payload round-trip and not a bare reflection"))

    # Only an error if the whole procedure never asserts content anywhere.
    if status_only_steps and not content_proof_anywhere:
        names = ", ".join(sid for sid, _ in status_only_steps)
        out.append(Finding(
            "status_only_2xx", "error", "<document>",
            f"no step asserts on content; only success statuses ({names}) — an "
            "error page, a redirect and a stack trace all satisfy this"))

    if not dynamic_seen:
        out.append(Finding(
            "no_dynamic_evidence", "warn", "<document>",
            "no assertion references ${edge.*} or ${steps.*} — nothing ties the "
            "proof to this scenario's values"))

    joined = " ".join(all_text)
    for name, sid in declared_outputs.items():
        if f"steps.{sid}.{name}" not in joined and \
                not any(f"steps.{sid}.{name}" in _sent_blob(s.get("action") or {})
                        for s in steps if isinstance(s, dict)):
            out.append(Finding("unused_output", "warn", sid,
                               f"captures output {name!r} that no later step "
                               "uses — the chain may not be chained"))

    out.sort(key=lambda f: (0 if f.severity == "error" else 1, f.step, f.rule))
    return out


def lint_file(path: Path) -> list[Finding]:
    try:
        doc = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        return [Finding("unparsed", "error", "<document>", f"invalid YAML: {exc}")]
    return lint_procedure(doc)


def has_errors(findings: list[Finding]) -> bool:
    return any(f.severity == "error" for f in findings)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="procedure-lint")
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--warn-as-error", action="store_true")
    args = ap.parse_args(argv)

    files: list[Path] = []
    for p in args.paths:
        files.extend(sorted(p.rglob("*.y*ml")) if p.is_dir() else [p])

    report, failed = {}, False
    for f in files:
        findings = lint_file(f)
        report[str(f)] = [asdict(x) for x in findings]
        bad = has_errors(findings) or (args.warn_as_error and findings)
        failed |= bad
        if not args.as_json:
            print(f"{'FAIL' if bad else 'ok':4}  {f}")
            for x in findings:
                print(x.line())
    if args.as_json:
        print(json.dumps(report, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
