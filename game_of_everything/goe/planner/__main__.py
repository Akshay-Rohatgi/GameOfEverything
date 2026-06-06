"""CLI entry point: python -m goe.planner "..."

Usage:
    python -m goe.planner "web app with SQL injection leading to credential theft"
    python -m goe.planner "..." --output graph.yaml
    python -m goe.planner "..." --verbose
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
    args = parser.parse_args()

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

    if args.output:
        args.output.write_text(yaml_str)
        print(f"Graph written to {args.output}")
    else:
        print(yaml_str)


if __name__ == "__main__":
    main()
