"""CLI entry point: python -m goe.planner "..."

Usage:
    python -m goe.planner "web app with SQL injection leading to credential theft"
    python -m goe.planner "..." --output graph.yaml
    python -m goe.planner "..." --verbose
    python -m goe.planner "..." --artifacts   # save conversation + graph to artifacts/
"""

import argparse
import sys
from pathlib import Path

from goe.planner.pipeline import plan


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plan an attack graph from a natural language request"
    )
    parser.add_argument("request", help="Natural language attack scenario description")
    parser.add_argument(
        "--output", "-o", type=Path, default=None,
        help="Output YAML path (default: stdout)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print planning step progress",
    )
    parser.add_argument(
        "--visualize", action="store_true",
        help="Print ASCII graph visualization after planning (uses graph-easy if available)",
    )
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

    from goe.artifacts.run import artifact_run

    command = " ".join(sys.argv)
    with artifact_run("planner", command, capture=args.artifacts) as (run_dir, _session):
        result = plan(args.request, verbose=args.verbose)

        if not result.success:
            print(
                f"Planning failed after {result.attempts} attempt(s).",
                file=sys.stderr,
            )
            for v in result.final_violations:
                entity_ctx = f" (entity: {v.entity_id})" if v.entity_id else ""
                edge_ctx = f" (edge: {v.edge_id})" if v.edge_id else ""
                print(f"  [{v.check}]{entity_ctx}{edge_ctx} {v.message}", file=sys.stderr)
            sys.exit(1)

        if args.visualize:
            from goe.graph.visualize import render_fancy
            print(render_fancy(result.graph))
            print()

        yaml_str = result.graph.to_yaml_str()

        # Persist the graph into the run-dir when capturing artifacts
        if run_dir is not None and result.graph:
            (run_dir / "graph.yaml").write_text(yaml_str, encoding="utf-8")

    if args.output:
        args.output.write_text(yaml_str)
        print(f"Graph written to {args.output}")
    else:
        print(yaml_str)


if __name__ == "__main__":
    main()
