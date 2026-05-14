from typing import Dict, Optional, List
from pydantic import BaseModel, Field
from game_of_everything.models import (
    ParsedRequest, MappedRequest, MappedAtom, GeneratedSnippet, TestResult,
    SynthesizedScenario, ResolvedCustomApp, ResolvedPresetApp,
    NetworkTopology, ChainTestResult,
)


class GoEState(BaseModel):
    run_id: Optional[str] = None
    raw_request: Optional[str] = None
    synthesized_scenario: Optional[SynthesizedScenario] = None
    parsed_request: Optional[ParsedRequest] = None
    resolved_custom_apps: List[ResolvedCustomApp] = []
    resolved_preset_apps: List[ResolvedPresetApp] = []
    mapped_request: Optional[MappedRequest] = None
    sequenced_request: Optional[List[MappedAtom]] = None
    generated_snippets: Optional[List[GeneratedSnippet]] = None
    test_results: Optional[List[TestResult]] = None
    final_script: Optional[str] = None
    output_path: Optional[str] = None

    # Multi-box topology (Phase 1+)
    topology: Optional[NetworkTopology] = None
    # box_states holds per-box GoEState during run_box_pipelines only.
    # Excluded from serialization to avoid circular reference in crewAI
    # flow state JSON encoding — results are extracted into deploy_scripts.
    box_states: Dict[str, "GoEState"] = Field(default_factory=dict, exclude=True)
    deploy_scripts: Dict[str, str] = {}
    chain_test_results: List[ChainTestResult] = []
    credential_warnings: List[str] = []
