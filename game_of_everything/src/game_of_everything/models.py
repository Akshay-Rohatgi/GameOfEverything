from typing import Dict, List, Optional
import json
from pydantic import BaseModel, field_validator


class CustomVector(BaseModel):
    """Inputs to CustomAppFlow — which vulnerabilities, runtime, and attack goals to use.

    A single CustomVector can carry multiple vuln atoms and attack goals when
    the vulnerabilities are meant to chain within one application (e.g. file
    upload + LFI → RCE).  The legacy singular fields (vuln_atom_id,
    attack_chain_goal) are accepted for backward compatibility and coerced
    into the list forms automatically.
    """
    vuln_atom_ids: List[str]                    # e.g. ["sqli_union"] or ["file_upload_bypass", "path_traversal_lfi"]
    attack_chain_goals: List[str]               # e.g. ["credential_theft"] or ["rce_via_webshell", "lfi_to_rce"]
    runtime_id: str                             # e.g. "apache_php", "flask", "express"
    install_path: str = "/var/www/html/app"
    port: int = 80
    db_name: Optional[str] = None
    db_user: Optional[str] = None
    db_password: Optional[str] = None
    seed_username: Optional[str] = None         # OS user whose creds are seeded into the app DB
    seed_password: Optional[str] = None
    synthesis_context: str = ""                 # From SynthesizedScenario.custom_app_scope

    # --- Backward-compat: accept singular fields and coerce to lists ----------

    @field_validator("vuln_atom_ids", mode="before")
    @classmethod
    def _coerce_vuln_atom_ids(cls, v):
        if isinstance(v, str):
            return [v]
        return v

    @field_validator("attack_chain_goals", mode="before")
    @classmethod
    def _coerce_attack_chain_goals(cls, v):
        if isinstance(v, str):
            return [v]
        return v

    def __init__(self, **data):
        # Allow callers to pass the old singular field names
        if "vuln_atom_id" in data and "vuln_atom_ids" not in data:
            data["vuln_atom_ids"] = data.pop("vuln_atom_id")
        if "attack_chain_goal" in data and "attack_chain_goals" not in data:
            data["attack_chain_goals"] = data.pop("attack_chain_goal")
        super().__init__(**data)

    # --- Convenience properties -----------------------------------------------

    @property
    def vuln_atom_id(self) -> str:
        """Primary vuln atom id (first in the list). Used for display and single-vuln compat."""
        return self.vuln_atom_ids[0]

    @property
    def attack_chain_goal(self) -> str:
        """Primary attack chain goal (first in the list)."""
        return self.attack_chain_goals[0]

    @property
    def display_name(self) -> str:
        """Human-readable label for logging: 'file_upload_bypass+path_traversal_lfi'."""
        return "+".join(self.vuln_atom_ids)


class PresetVector(BaseModel):
    """Inputs to PresetAppFlow — which pre-built app and vulnerability profile to deploy."""
    preset_id: str                              # e.g. "wordpress", "phpbb"
    vuln_profile_ids: List[str]                 # e.g. ["wp_default_creds"]
    port: int = 80
    admin_user: Optional[str] = None
    admin_password: Optional[str] = None
    db_name: Optional[str] = None
    db_user: Optional[str] = None
    db_password: Optional[str] = None
    synthesis_context: str = ""
    extra_vars: Dict[str, str] = {}             # App-specific overrides (plugin slug, etc.)


class ResolvedPresetApp(BaseModel):
    """A validated preset app ready to be sequenced into the deploy script."""
    vector: PresetVector
    stack_id: str
    deploy_snippet: str
    testing_snippet: str
    attack_snippet: str
    validation_passed: bool


class SharedSecret(BaseModel):
    """A concrete credential shared across boxes in the topology.

    Defined early so SynthesizedScenario can reference it for multi-box scenarios.
    """
    key: str                        # "pivot_web_to_db"
    value: str                      # "Summer2024!"
    description: str                # Human-readable: what this secret is
    source_box: str                 # box_id where attacker discovers this
    target_box: str                 # box_id where this secret grants access
    target_user: str                # Username on the target: "dbadmin"
    access_method: str              # "ssh", "web_login", "smb", "ftp", "mysql"


class KillChainStep(BaseModel):
    """One step in the tactical kill-chain display."""
    tag: str                        # ENUM, WEB, CRED, RCE, LPE, LAT, PERSIST, EXFIL
    action: str                     # "Upload PHP Webshell -> Achieve www-data shell"


class BoxSpec(BaseModel):
    """Per-box description produced by synthesize_scenario for multi-box scenarios.

    Contains the same pipeline inputs as a single-box SynthesizedScenario but
    scoped to one machine. Converted to BoxDefinition when building the topology.
    """
    box_id: str                     # Unique: "webserver", "db-server"
    hostname: str                   # Network hostname: "web01"
    role: str                       # Narrative: "Public-facing web server"
    misconfig_scope: str            # Attacker-facing vulnerability description for this box
    custom_app_scope: Optional[str] = None
    custom_vectors: List[CustomVector] = []
    preset_vectors: List["PresetVector"] = []
    services: List[str] = []        # ["ssh:22", "http:80", "mysql:3306"]
    # Tactical display fields populated by the synthesis agent
    attack_vector: Optional[str] = None   # "Unauth Web RCE -> SUID Data Exfil"
    goal: Optional[str] = None            # "Harvest internal credentials for [backup01]"


class SynthesizedScenario(BaseModel):
    """
    Fully elaborated scenario produced by the synthesis step.
    Resolves all implicit decisions before any parsing or mapping happens.
    """
    narrative: str                              # Full box description, all config decisions explicit
    attack_narrative: str                       # End-to-end attacker path
    shared_resources: List[str]                 # e.g. "MySQL serves app backend + misconfig surface"
    explicit_decisions: List[str]               # What the LLM decided that wasn't in the prompt
    misconfig_scope: str = ""                   # Single-box: pipeline input. Multi-box: unused (see boxes)
    custom_app_scope: Optional[str] = None      # Single-box: custom app description. Multi-box: unused
    custom_vectors: List[CustomVector] = []     # Single-box: structured vectors. Multi-box: unused
    preset_vectors: List[PresetVector] = []    # Single-box: preset app vectors. Multi-box: unused
    num_boxes: int = 1                          # Number of boxes required to build this scenario
    # Multi-box: per-box pipeline descriptions (populated when num_boxes > 1)
    boxes: List[BoxSpec] = []
    shared_secrets: List[SharedSecret] = []     # Cross-box credentials (the dependency map)
    # Tactical display fields (single-box scope; for multi-box the per-box
    # attack_vector/goal live on each BoxSpec).
    attack_vector: Optional[str] = None
    goal: Optional[str] = None
    kill_chain: List[KillChainStep] = []        # Full numbered operation plan across all boxes


class ParsedRequest(BaseModel):
    """
    Represents the parsed user request, broken down into logical sections.
    """
    initial_prompt: str
    context: str
    initial_access_vectors: List[str]
    post_exploitation_goals: List[str]

class MappedAtom(BaseModel):
    """
    Represents a specific Atom mapped from the user request.
    """
    name: str
    context: str
    parameters: Optional[dict] = None

    @field_validator('parameters', mode='before')
    @classmethod
    def coerce_parameters(cls, v):
        """Tolerate LLM outputs that append backtick-markdown text after a JSON
        dict (e.g. '{}` \n2. `other_atom...'). json.JSONDecoder.raw_decode parses
        as much valid JSON as possible and ignores trailing characters."""
        if v is None or isinstance(v, dict):
            return v
        if isinstance(v, str):
            v_stripped = v.strip()
            if not v_stripped:
                return None
            try:
                return json.loads(v_stripped)
            except json.JSONDecodeError:
                try:
                    obj, _ = json.JSONDecoder().raw_decode(v_stripped)
                    if isinstance(obj, dict):
                        return obj
                except json.JSONDecodeError:
                    pass
                return {}
        return v

class MappedRequest(BaseModel):
    """
    Represents the request mapped to specific Atoms for each section.
    """
    section: str
    mapped_initial_access_atoms: List[MappedAtom]
    mapped_post_exploitation_goal_atoms: List[MappedAtom]

class GeneratedSnippet(BaseModel):
    """
    Represents the code snippet generated for a specific Atom.
    """
    atom_name: str
    code_snippet: str
    testing_snippet: str
    attack_snippet: Optional[str] = None  # Layer 2: adversarial probe from attacker container
    mapped_atom: MappedAtom
    validated: bool = False

    def set_validated(self, validated: bool):
        self.validated = validated


class TestVerdict(BaseModel):
    """
    LLM-produced judgment on whether a command's output indicates success
    for a given atom's expected state or exploit.
    """
    passed: bool
    reasoning: str


class DiagnosticResult(BaseModel):
    """
    Output from the Diagnostic Agent's analysis and fix attempt for a failing snippet.
    """
    fixed_code_snippet: str
    fixed_testing_snippet: str
    diagnosis: str           # what went wrong and what was changed
    confidence: str          # "high", "medium", or "low"


class AttackDiagnosticResult(BaseModel):
    """Output from the Attack Agent's fix of a failing L2 attack snippet."""
    fixed_attack_snippet: str
    diagnosis: str
    confidence: str          # "high", "medium", or "low"


class AttackOrchestratorResult(BaseModel):
    """Output from the Attack Orchestrator's L1+L2 validation."""
    l1_passed: bool
    l2_passed: bool
    l1_evidence: str          # what exec_in_target returned for testing_snippet
    l2_evidence: str          # browser output or CLI output proving exploit success/failure
    reasoning: str            # orchestrator's synthesis of pass/fail
    used_browser: bool = False


class TestResult(BaseModel):
    """
    Captures the full test outcome for a single snippet across both layers.
    """
    atom_name: str
    layer1_verdict: TestVerdict
    layer2_verdicts: Optional[List[TestVerdict]] = None  # one per cumulative probe (snippets 0..N)
    diagnostic_results: Optional[List[DiagnosticResult]] = None  # all diagnosis attempts (L1 retries + L2 diag)
    error: Optional[str] = None

class GeneratedApp(BaseModel):
    """All files and snippets produced by the generate_app step."""
    app_filename: str           # e.g. "app.php", "app.py", "app.js"
    app_source: str             # Full source of the single app file
    schema_sql: Optional[str] = None   # CREATE TABLE statements (None if no DB)
    seed_sql: Optional[str] = None     # INSERT seed data (None if no DB)
    setup_db_sh: Optional[str] = None  # Script to create DB, user, apply schema and seed (None if no DB)
    deploy_snippet: str         # Bash to deploy the app and start the web server
    testing_snippet: str        # Layer 1: internal state check
    attack_objective: str       # Layer 2: structured natural language task for Attack Orchestrator

    # Tolerate old checkpoints that have attack_snippet field
    model_config = {"extra": "ignore"}


class ResolvedCustomApp(BaseModel):
    """A generated and validated custom app ready to be sequenced into the deploy script."""
    vector: CustomVector
    deploy_snippet: str
    testing_snippet: str
    attack_snippet: str = ""  # Deprecated: orchestrator uses attack_objective from GeneratedApp instead
    validation_passed: bool


class CustomAppState(BaseModel):
    """State object for CustomAppFlow."""
    vector: Optional[CustomVector] = None
    vuln_atom_contents: List[str] = []          # Full atom markdown per vuln_atom_id
    attack_goals: List[dict] = []               # Loaded attack goal YAMLs
    web_runtime: Optional[dict] = None          # Loaded web runtime YAML
    generated_app: Optional[GeneratedApp] = None
    layer1_verdict: Optional["TestVerdict"] = None
    layer2_verdict: Optional["TestVerdict"] = None
    generate_attempts: int = 0
    resolved: Optional[ResolvedCustomApp] = None


class SequencedRequest(BaseModel):
    """
    Represents the ordered list of Atoms to be executed.
    """
    atoms: List[MappedAtom]

class GeneratedSnippets(BaseModel):
    """
    Represents a collection of generated snippets.
    """
    snippets: List[GeneratedSnippet]


# ---------------------------------------------------------------------------
# Multi-box topology models
# ---------------------------------------------------------------------------

# SharedSecret is defined above SynthesizedScenario so it can be used there.
# The class is intentionally placed early in the file.


class BoxDefinition(BaseModel):
    """One machine in the topology."""
    box_id: str                     # Unique: "webserver", "db-server"
    hostname: str                   # Network hostname: "web01"
    role: str                       # Narrative: "Public-facing web server"
    os: str = "ubuntu:22.04"        # Base image

    # Per-box pipeline inputs (same fields as SynthesizedScenario per box)
    misconfig_scope: str
    custom_app_scope: Optional[str] = None
    custom_vectors: List[CustomVector] = []
    preset_vectors: List[PresetVector] = []

    # What services this box exposes (for docker-compose ports + README)
    services: List[str] = []        # ["ssh:22", "http:80", "mysql:3306"]


class PivotLink(BaseModel):
    """Directed edge: how the attacker moves from one box to the next."""
    from_box: str
    to_box: str
    method: str                     # "credential_reuse", "ssh_key_reuse", "tunnel"
    secret_ref: Optional[str] = None  # Key into SharedSecret
    description: str                # "SQLi on web app → extract dbadmin:Summer2024! → SSH to db-server"


class ChainProbe(BaseModel):
    """One step in the Layer 3 attack chain validation.

    For MVP, these are generated deterministically from PivotLinks + SharedSecrets.
    No LLM needed for simple credential-reuse pivots.
    """
    step: int                       # Ordinal: 1, 2, 3...
    from_container: str             # "attacker" or a box_id
    target_hostname: str            # Hostname to attack
    command: str                    # Bash command to execute
    success_pattern: str            # Regex that must appear in stdout for pass


class NetworkTopology(BaseModel):
    """Full multi-box scenario — the single synthesis output for all requests."""
    scenario_name: str              # "Corporate DMZ Breach"
    narrative: str                  # Full scenario description
    attack_narrative: str           # End-to-end attacker path across ALL boxes
    entry_point: List[str]           # box_ids the attacker can start from
    boxes: List[BoxDefinition]
    pivots: List[PivotLink]
    shared_secrets: List[SharedSecret]
    chain_probes: List[ChainProbe] = []   # Generated post-synthesis, not by LLM

    # Synthesis metadata
    shared_resources: List[str]
    explicit_decisions: List[str]


class ChainTestResult(BaseModel):
    """Outcome of a single chain probe execution."""
    step: int
    command: str = ""
    passed: bool
    stdout: str = ""
    stderr: str = ""


def single_box_scenario_to_topology(
    scenario: SynthesizedScenario,
    scenario_name: str = "Single Box Scenario",
) -> NetworkTopology:
    """Wrap a SynthesizedScenario into a single-box NetworkTopology."""
    box = BoxDefinition(
        box_id="target",
        hostname="target",
        role=scenario.narrative,
        misconfig_scope=scenario.misconfig_scope,
        custom_app_scope=scenario.custom_app_scope,
        custom_vectors=scenario.custom_vectors,
        preset_vectors=scenario.preset_vectors,
        services=[],
    )
    return NetworkTopology(
        scenario_name=scenario_name,
        narrative=scenario.narrative,
        attack_narrative=scenario.attack_narrative,
        entry_point=["target"],
        boxes=[box],
        pivots=[],
        shared_secrets=[],
        chain_probes=[],
        shared_resources=scenario.shared_resources,
        explicit_decisions=scenario.explicit_decisions,
    )


def scenario_to_topology(scenario: SynthesizedScenario) -> NetworkTopology:
    """Convert a SynthesizedScenario to a NetworkTopology.

    When scenario.boxes is populated (multi-box), creates one BoxDefinition per
    BoxSpec and injects shared_secrets into the topology for cross-box dependency
    maps. When scenario.boxes is empty (single-box), delegates to the legacy
    single_box_scenario_to_topology path.
    """
    if not scenario.boxes:
        return single_box_scenario_to_topology(scenario)

    boxes = [
        BoxDefinition(
            box_id=spec.box_id,
            hostname=spec.hostname,
            role=spec.role,
            misconfig_scope=spec.misconfig_scope,
            custom_app_scope=spec.custom_app_scope,
            custom_vectors=spec.custom_vectors,
            preset_vectors=spec.preset_vectors,
            services=spec.services,
        )
        for spec in scenario.boxes
    ]
    entry_point = [scenario.boxes[0].box_id]
    scenario_name = (scenario.narrative[:60].rstrip() + "...") if len(scenario.narrative) > 60 else scenario.narrative

    return NetworkTopology(
        scenario_name=scenario_name,
        narrative=scenario.narrative,
        attack_narrative=scenario.attack_narrative,
        entry_point=entry_point,
        boxes=boxes,
        pivots=[],
        shared_secrets=scenario.shared_secrets,
        chain_probes=[],
        shared_resources=scenario.shared_resources,
        explicit_decisions=scenario.explicit_decisions,
    )
