"""LLM-as-a-judge for semantic graph validation.

Use when golden tests are too brittle (e.g., edge structure varies legitimately).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goe.graph.models import EntityGraph

_JUDGE_SYSTEM_PROMPT = """You are an expert security researcher evaluating attack graph quality.

Given a user's scenario request and a planner-generated attack graph, assess whether
the graph is semantically correct and complete.

Focus on:
1. **Attack path viability**: Can the operator reach the goal via these edges?
2. **Missing critical edges**: Are there obvious connections the planner missed?
3. **Nonsensical edges**: Are there edges that don't make sense for the scenario?
4. **Edge type appropriateness**: Do edge types match their purpose?

Do NOT penalize:
- Different edge naming (e.g., "sqli_to_creds" vs "database_credentials")
- Terminal edges with to_entity: null (these are valid for final objectives)
- Extra entities/edges if they're logically sound

Output ONLY valid JSON with this schema:
{
  "logical_path": true/false,
  "missing_edges": ["description"],
  "nonsensical_edges": ["edge_id: reason"],
  "overall_quality": "good" | "acceptable" | "poor",
  "reasoning": "brief explanation"
}"""


def format_graph_for_judge(graph: EntityGraph) -> str:
    """Format the graph in a readable way for LLM evaluation."""
    lines = ["## Entities"]
    for entity in graph.entities:
        lines.append(f"- {entity.id}: {entity.description}")
        lines.append(f"  Runtime: {entity.runtime.value}")
        lines.append(f"  Atoms: {', '.join(entity.atoms or [])}")
        if entity.requires:
            lines.append(f"  Requires: {', '.join(r.edge_id for r in entity.requires)}")
        if entity.provides:
            lines.append(f"  Provides: {', '.join(entity.provides)}")

    lines.append("\n## Edges")
    for edge in graph.edges:
        to_str = edge.to_entity or "(terminal)"
        lines.append(f"- {edge.id}: {edge.from_entity} → {to_str}")
        lines.append(f"  Type: {edge.type}")
        param_strs = [f"{k}={v.structural}" for k, v in edge.params.items() if v.structural]
        if param_strs:
            lines.append(f"  Params: {', '.join(param_strs)}")

    return "\n".join(lines)


def judge_graph_quality(
    graph: EntityGraph,
    request: str,
    model: str = "anthropic.claude-haiku-4-5-20251001-v1:0",
) -> dict:
    """Use LLM to evaluate if the graph makes semantic sense for the scenario.

    Returns:
        Dict with keys: logical_path (bool), missing_edges (list),
        nonsensical_edges (list), overall_quality (str), reasoning (str)
    """
    from goe.bedrock import call

    graph_repr = format_graph_for_judge(graph)

    user_msg = f"""## Scenario Request

"{request}"

## Planner-Generated Graph

{graph_repr}

Evaluate this graph for the given scenario."""

    response = call(
        model_id=model,
        system=_JUDGE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
        caller="eval.llm_judge",
    )

    # Strip markdown fences if present
    response = response.strip()
    if response.startswith("```"):
        response = response.split("\n", 1)[1] if "\n" in response else response
        response = response.rsplit("```", 1)[0]

    return json.loads(response)


def print_judge_result(result: dict) -> None:
    """Pretty-print LLM judge results."""
    print("\n  LLM Judge Evaluation:")

    # Logical path
    logical_path = result.get("logical_path", False)
    status = "✓" if logical_path else "✗"
    print(f"    Logical attack path: {status} {'Yes' if logical_path else 'No'}")

    # Missing edges
    missing_edges = result.get("missing_edges") or []
    if missing_edges:
        print(f"    Missing edges ({len(missing_edges)}):")
        for edge in missing_edges[:3]:
            print(f"      - {edge}")
    else:
        print("    Missing edges: None")

    # Nonsensical edges
    nonsensical_edges = result.get("nonsensical_edges") or []
    if nonsensical_edges:
        print(f"    Nonsensical edges ({len(nonsensical_edges)}):")
        for edge in nonsensical_edges[:3]:
            print(f"      - {edge}")
    else:
        print("    Nonsensical edges: None")

    # Overall quality
    overall_quality = result.get("overall_quality", "unknown")
    quality_emoji = {"good": "✓", "acceptable": "~", "poor": "✗"}
    emoji = quality_emoji.get(overall_quality, "?")
    print(f"    Overall quality: {emoji} {overall_quality}")

    # Reasoning
    if result.get("reasoning"):
        print(f"    Reasoning: {result['reasoning']}")
