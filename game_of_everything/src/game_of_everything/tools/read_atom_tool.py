from pathlib import Path
from typing import Type
from pydantic import BaseModel, Field
from crewai.tools import BaseTool

SCRIPT_DIR = Path(__file__).parent.parent.parent
PROJECT_DIR = SCRIPT_DIR.parent
ATOMS_DIR = PROJECT_DIR / "atoms"


class ReadAtomInput(BaseModel):
    atom_name: str = Field(
        ...,
        description="The exact atom id to read (e.g. 'samba_insecure_share'). Do not include the .md extension.",
    )


class ReadAtomTool(BaseTool):
    name: str = "read_atom"
    description: str = (
        "Read the full markdown content of a specific vulnerability Atom by its id. "
        "Use this to retrieve the Logic Requirements, Synthesis Guidance, and Testing Guidance "
        "for an atom before generating its script snippet. "
        "Pass the exact atom id (e.g. 'samba_insecure_share') to the 'atom_name' parameter."
    )
    args_schema: Type[BaseModel] = ReadAtomInput

    def _run(self, atom_name: str) -> str:
        atom_name = atom_name.strip().removesuffix(".md")
        atom_path = ATOMS_DIR / f"{atom_name}.md"
        if not atom_path.exists():
            available = [p.stem for p in ATOMS_DIR.glob("*.md")]
            return (
                f"Error: Atom '{atom_name}' not found at {atom_path}. "
                f"Available atoms: {available}"
            )
        return atom_path.read_text(encoding="utf-8")
