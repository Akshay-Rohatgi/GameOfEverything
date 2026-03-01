from typing import Dict, List, Optional
from pydantic import BaseModel

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


class TestResult(BaseModel):
    """
    Captures the full test outcome for a single snippet across both layers.
    """
    atom_name: str
    layer1_verdict: TestVerdict
    layer2_verdicts: Optional[List[TestVerdict]] = None  # one per cumulative probe (snippets 0..N)
    error: Optional[str] = None

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
