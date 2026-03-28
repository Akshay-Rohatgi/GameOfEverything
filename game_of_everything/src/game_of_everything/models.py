from typing import Dict, List, Optional
import json
from pydantic import BaseModel, field_validator


class CustomVector(BaseModel):
    """Inputs to CustomAppFlow — which vulnerability, runtime, and attack goal to use."""
    vuln_atom_id: str                           # e.g. "sqli_union", "ssti_jinja2"
    attack_chain_goal: str                      # e.g. "credential_theft", "rce_via_webshell"
    runtime_id: str                             # e.g. "apache_php", "flask", "express"
    install_path: str = "/var/www/html/app"
    port: int = 80
    db_name: Optional[str] = None
    db_user: Optional[str] = None
    db_password: Optional[str] = None
    seed_username: Optional[str] = None         # OS user whose creds are seeded into the app DB
    seed_password: Optional[str] = None
    synthesis_context: str = ""                 # From SynthesizedScenario.custom_app_scope


class SynthesizedScenario(BaseModel):
    """
    Fully elaborated scenario produced by the synthesis step.
    Resolves all implicit decisions before any parsing or mapping happens.
    """
    narrative: str                              # Full box description, all config decisions explicit
    attack_narrative: str                       # End-to-end attacker path
    shared_resources: List[str]                 # e.g. "MySQL serves app backend + misconfig surface"
    explicit_decisions: List[str]               # What the LLM decided that wasn't in the prompt
    misconfig_scope: str                        # What to hand to the misconfig pipeline
    custom_app_scope: Optional[str] = None      # Human-readable description of custom app(s)
    custom_vectors: List[CustomVector] = []     # Structured vectors for CustomAppFlow


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
    attack_snippet: str         # Layer 2: external exploit from attacker container


class ResolvedCustomApp(BaseModel):
    """A generated and validated custom app ready to be sequenced into the deploy script."""
    vector: CustomVector
    deploy_snippet: str
    testing_snippet: str
    attack_snippet: str
    validation_passed: bool


class CustomAppState(BaseModel):
    """State object for CustomAppFlow."""
    vector: Optional[CustomVector] = None
    vuln_atom_content: Optional[str] = None    # Full atom markdown from web_vuln_atoms ChromaDB
    attack_goal: Optional[dict] = None          # Loaded attack goal YAML
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
