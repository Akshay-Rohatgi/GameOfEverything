"""Visualize a saved graph YAML: python -m goe.graph <graph.yaml>"""

import argparse
import sys
from pathlib import Path

from goe.graph.models import EntityGraph
from goe.graph.validator import validate
from goe.graph.visualize import render, render_fancy


def main() -> None:
    parser = argparse.ArgumentParser(description="Visualize an EntityGraph YAML file")
    parser.add_argument("graph", type=Path, help="Path to graph YAML file")
    parser.add_argument("--validate", action="store_true", help="Also run validator")
    parser.add_argument("--simple", action="store_true", help="Use plain ASCII (no graph-easy)")
    args = parser.parse_args()

    graph = EntityGraph.from_yaml(args.graph)
    print(render(graph) if args.simple else render_fancy(graph))

    if args.validate:
        result = validate(graph)
        if result.valid:
            print("✓ graph is valid")
        else:
            print(f"✗ {len(result.violations)} violation(s):")
            for v in result.violations:
                print(f"  [{v.check}] {v.message}")
            sys.exit(1)


if __name__ == "__main__":
    main()
