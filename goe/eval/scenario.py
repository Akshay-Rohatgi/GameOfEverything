"""Scenario reliability eval — measure end-to-end multi-host build *success rate*.

Unlike the ``build`` suite (which runs ``build_entity`` on a single pre-baked entity
fixture), this suite drives the **full** prompt→pipeline path exactly as ``goe run``
does: ``planner`` → per-host OS assignment → scheduled multi-host build → cross-host
chain test. Planning, OS assignment and cross-host wiring are therefore all under test.

Because the pipeline is stochastic, a single run only yields pass/fail; this suite runs
a prompt ``repeats`` times and aggregates *where* it fails into ranked buckets, so the
"fails 2 times in 5" footguns Week-7 cares about become visible and measurable
before/after a fix.

Prompts are **input** (CLI ``--request`` / ``--requests <file>``), never hardcoded here.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

# ---- Failure stages (pipeline order; earliest-failing stage wins) -----------
STAGE_PLAN = "plan_failed"      # planner produced no graph (violations / LLM failure)
STAGE_BUILD = "build_failed"    # one or more entities failed to build
STAGE_EDGE_GAP = "edge_gap"     # a consumed cross-host edge param never got a value
STAGE_CHAIN = "chain_failed"    # build OK but the cross-host exploit chain failed
STAGE_CRASH = "crash"           # the run raised (orchestration bug)
STAGE_SUCCESS = "success"

_FAIL_STAGES = [STAGE_PLAN, STAGE_BUILD, STAGE_EDGE_GAP, STAGE_CHAIN, STAGE_CRASH]


def _head(text, n: int = 160) -> str:
    """First non-empty line of ``text``, truncated — for compact failure detail."""
    if not text:
        return ""
    for line in str(text).splitlines():
        line = line.strip()
        if line:
            return line[:n]
    return ""


@dataclass
class RunOutcome:
    """Classification of a single pipeline run against one prompt."""

    index: int
    stage: str
    success: bool
    distro_mix: dict        # system_id -> normalized distro
    n_systems: int
    n_entities: int
    n_built: int
    failures: list          # list[dict]: concrete failure signatures (see classify_run)
    attempts: dict          # entity_id -> attempt count
    duration_s: float = 0.0


@dataclass
class ScenarioReport:
    """Aggregated reliability of one prompt over ``repeats`` runs."""

    request: str
    repeats: int
    timestamp: str
    outcomes: list = field(default_factory=list)          # list[RunOutcome]
    success_count: int = 0
    stage_counts: dict = field(default_factory=dict)       # stage -> count
    signature_counts: list = field(default_factory=list)   # [(signature, count, example_detail)]
    distro_mix_rates: list = field(default_factory=list)   # [(mix_str, passed, total)]
    mean_attempts: float = 0.0
    max_attempts: int = 0
    total_tokens: int = 0
    total_latency_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.success_count / self.repeats if self.repeats else 0.0


def _distro_mix(graph) -> dict:
    if graph is None:
        return {}
    return {s.id: s.os for s in graph.systems}


def classify_run(rr, *, index: int = 0, duration_s: float = 0.0) -> RunOutcome:
    """Reduce a ``RunResult`` to the earliest-failing stage + concrete signatures."""
    graph = rr.graph
    mix = _distro_mix(graph)
    n_systems = len(graph.systems) if graph else 0
    n_entities = len(graph.entities) if graph else 0

    attempts: dict = {}
    n_built = 0
    if graph is not None:
        for r in rr.results:
            attempts[r.id] = getattr(r, "attempts", 1)
            if r.status.value == "PASSED":
                n_built += 1

    failures: list = []

    if graph is None:
        stage = STAGE_PLAN
        for v in (rr.final_violations or [])[:5]:
            failures.append({
                "stage": STAGE_PLAN,
                "signature": f"plan/violation:{getattr(v, 'check', '?')}",
                "detail": _head(getattr(v, "message", "")),
            })
        if not failures:
            failures.append({"stage": STAGE_PLAN, "signature": "plan/failed", "detail": ""})

    elif rr.failed:
        stage = STAGE_BUILD
        cat_by_id = {r.id: r.failure_category for r in rr.results}
        for eid, reason in rr.failed.items():
            ent = graph.entity_by_id(eid)
            runtime = ent.runtime.value if ent else "?"
            distro = mix.get(ent.system_id, "?") if ent else "?"
            cat = cat_by_id.get(eid) or "uncategorized"
            failures.append({
                "stage": STAGE_BUILD,
                "signature": f"build/{runtime}/{distro}/{cat}",
                "entity": eid,
                "runtime": runtime,
                "distro": distro,
                "category": cat,
                # Build failures carry the diagnostician's reasoning — keep more of it
                # than the compact default so the footgun is legible without a re-run.
                "detail": _head(reason, 400),
            })

    elif rr.edge_gaps:
        stage = STAGE_EDGE_GAP
        for gap in rr.edge_gaps:
            failures.append({
                "stage": STAGE_EDGE_GAP,
                "signature": f"edge_gap/{gap}",
                "detail": gap,
            })

    elif rr.chain_test is not None and rr.chain_test.status.value != "PASSED":
        stage = STAGE_CHAIN
        failures.append({
            "stage": STAGE_CHAIN,
            "signature": f"chain/{rr.chain_test.broken_edge or '?'}",
            "broken_edge": rr.chain_test.broken_edge,
            "detail": _head(rr.chain_test.reason),
        })

    else:
        stage = STAGE_SUCCESS

    return RunOutcome(
        index=index,
        stage=stage,
        success=bool(rr.success),
        distro_mix=mix,
        n_systems=n_systems,
        n_entities=n_entities,
        n_built=n_built,
        failures=failures,
        attempts=attempts,
        duration_s=duration_s,
    )


def _mix_str(mix: dict) -> str:
    if not mix:
        return "(no graph)"
    return ", ".join(f"{sid}={os}" for sid, os in sorted(mix.items()))


def build_scenario_report(request: str, outcomes: list, session=None) -> ScenarioReport:
    """Aggregate per-run outcomes into a ranked reliability report."""
    repeats = len(outcomes)
    success_count = sum(1 for o in outcomes if o.success)

    stage_counts: dict = Counter(o.stage for o in outcomes)

    # Rank concrete failure signatures across all runs, keeping one example detail.
    sig_counter: Counter = Counter()
    sig_example: dict = {}
    for o in outcomes:
        for f in o.failures:
            sig = f["signature"]
            sig_counter[sig] += 1
            if sig not in sig_example and f.get("detail"):
                sig_example[sig] = f["detail"]
    signature_counts = [
        (sig, cnt, sig_example.get(sig, "")) for sig, cnt in sig_counter.most_common()
    ]

    # Success rate per distro mix (surfaces OS-combo-specific weakness).
    mix_total: Counter = Counter()
    mix_pass: Counter = Counter()
    for o in outcomes:
        key = _mix_str(o.distro_mix)
        mix_total[key] += 1
        if o.success:
            mix_pass[key] += 1
    distro_mix_rates = [
        (key, mix_pass[key], mix_total[key]) for key in sorted(mix_total)
    ]

    all_attempts = [a for o in outcomes for a in o.attempts.values()]
    mean_attempts = sum(all_attempts) / len(all_attempts) if all_attempts else 0.0
    max_attempts = max(all_attempts) if all_attempts else 0

    total_tokens = 0
    total_latency_ms = 0.0
    if session is not None:
        s = session.summary()
        total_tokens = s.get("total_tokens", 0)
        total_latency_ms = s.get("total_latency_ms", 0.0)

    return ScenarioReport(
        request=request,
        repeats=repeats,
        timestamp=datetime.now().isoformat(timespec="seconds"),
        outcomes=outcomes,
        success_count=success_count,
        stage_counts=dict(stage_counts),
        signature_counts=signature_counts,
        distro_mix_rates=distro_mix_rates,
        mean_attempts=mean_attempts,
        max_attempts=max_attempts,
        total_tokens=total_tokens,
        total_latency_ms=total_latency_ms,
    )


def _print_run_line(o: RunOutcome) -> None:
    icon = "✓" if o.success else "✗"
    tail = ""
    if o.failures:
        tail = f" → {o.failures[0]['signature']}"
    print(
        f"  [{o.index + 1}] {icon} {o.stage:<12s} "
        f"built {o.n_built}/{o.n_entities} on {o.n_systems} host(s) "
        f"({o.duration_s:.0f}s){tail}"
    )


def print_scenario_report(report: ScenarioReport) -> None:
    pct = 100 * report.success_rate
    print(f"\n{'='*64}")
    print(f"Scenario reliability: {report.success_count}/{report.repeats} "
          f"({pct:.0f}%) succeeded")
    print(f"  prompt: {report.request}")
    print(f"  {report.timestamp}")
    print(f"{'='*64}")

    print("\n## Where runs ended (stage)")
    for stage in [STAGE_SUCCESS, *_FAIL_STAGES]:
        cnt = report.stage_counts.get(stage, 0)
        if cnt:
            print(f"    {stage:<14s} {cnt:>2d}/{report.repeats}")

    if report.signature_counts:
        print("\n## Top failure signatures (ranked)")
        for sig, cnt, detail in report.signature_counts:
            line = f"    {cnt:>2d}x  {sig}"
            if detail:
                line += f"\n          └─ {detail}"
            print(line)

    if len(report.distro_mix_rates) > 1 or (
        report.distro_mix_rates and report.distro_mix_rates[0][0] != "(no graph)"
    ):
        print("\n## Success rate by distro mix")
        for mix, passed, total in report.distro_mix_rates:
            print(f"    {passed}/{total}  {mix}")

    print("\n## Retry / cost")
    print(f"    Mean attempts/entity: {report.mean_attempts:.2f}  (max {report.max_attempts})")
    if report.total_tokens:
        print(f"    Total tokens: {report.total_tokens:,}  "
              f"latency: {report.total_latency_ms/1000:.0f}s")
    print(f"{'='*64}\n")


def run_scenario_eval(
    request: str,
    *,
    repeats: int = 5,
    os_override: str | None = None,
    verbose: bool = False,
    capture_artifacts: bool = False,
    run_dir=None,
) -> ScenarioReport:
    """Run the full pipeline against ``request`` ``repeats`` times and aggregate.

    Each run also persists its own ``output/<run_id>/`` package on disk (via the
    orchestrator's normal packaging), so any failing run can be inspected afterwards
    without re-running.
    """
    from goe.flow.orchestrator import run as flow_run
    from goe.metrics import start_session, end_session

    print(f"\n{'='*64}")
    print(f"Running scenario x{repeats}: {request}")
    if os_override:
        print(f"  --os {os_override} (OS pinned; OS-assignment step skipped)")
    print(f"{'='*64}")

    session = start_session(capture_artifacts=capture_artifacts, run_dir=run_dir)
    outcomes: list = []
    for i in range(repeats):
        t0 = time.time()
        try:
            rr = flow_run(request, verbose=verbose, os_override=os_override)
            outcome = classify_run(rr, index=i, duration_s=time.time() - t0)
        except Exception as e:  # orchestration bug — record, keep going
            import traceback
            traceback.print_exc()
            outcome = RunOutcome(
                index=i, stage=STAGE_CRASH, success=False, distro_mix={},
                n_systems=0, n_entities=0, n_built=0,
                failures=[{
                    "stage": STAGE_CRASH,
                    "signature": f"crash/{type(e).__name__}",
                    "detail": _head(str(e)),
                }],
                attempts={}, duration_s=time.time() - t0,
            )
        outcomes.append(outcome)
        _print_run_line(outcome)

    session = end_session()
    report = build_scenario_report(request, outcomes, session)
    print_scenario_report(report)
    return report


def load_requests(path) -> list[str]:
    """Read prompts from a file: one prompt per line; blanks and ``#`` lines ignored."""
    from pathlib import Path

    lines = Path(path).read_text().splitlines()
    out = []
    for ln in lines:
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(ln)
    return out


def run_scenario_suite(
    requests: list[str],
    *,
    repeats: int = 5,
    os_override: str | None = None,
    verbose: bool = False,
    capture_artifacts: bool = False,
    run_dir=None,
) -> list[ScenarioReport]:
    """Run several prompts and print a combined success-rate summary at the end."""
    reports = []
    for req in requests:
        reports.append(run_scenario_eval(
            req, repeats=repeats, os_override=os_override, verbose=verbose,
            capture_artifacts=capture_artifacts, run_dir=run_dir,
        ))

    if len(reports) > 1:
        print(f"\n{'#'*64}")
        print("# Suite summary")
        print(f"{'#'*64}")
        total_ok = sum(r.success_count for r in reports)
        total_runs = sum(r.repeats for r in reports)
        for r in reports:
            print(f"  {r.success_count}/{r.repeats} ({100*r.success_rate:.0f}%)  {r.request}")
        overall = 100 * total_ok / total_runs if total_runs else 0
        print(f"  {'-'*58}")
        print(f"  {total_ok}/{total_runs} ({overall:.0f}%)  OVERALL")
        print(f"{'#'*64}\n")

    return reports
