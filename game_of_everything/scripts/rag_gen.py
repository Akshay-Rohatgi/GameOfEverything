import os
import yaml
import chromadb
import boto3
from dotenv import load_dotenv
from pathlib import Path
from chromadb.utils.embedding_functions import AmazonBedrockEmbeddingFunction

load_dotenv()

# Path setup relative to this script
SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
ATOMS_DIR = PROJECT_DIR / "atoms"
CHROMA_DB_PATH = PROJECT_DIR / "src/game_of_everything" / "chroma_db"


aws_session = boto3.Session(
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", ""),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
    region_name=os.getenv("AWS_REGION", "us-east-1"),
)

bedrock_ef = AmazonBedrockEmbeddingFunction(
    session=aws_session,
    model_name="amazon.titan-embed-text-v2:0",
)

chroma_client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))

# Two collections — never cross-queried.
# goe_collection: misconfiguration atoms (atoms/*.md)
# web_vuln_atoms: web vulnerability atoms (atoms/web_vulnerabilities/*.md)
misconfig_collection = chroma_client.get_or_create_collection(
    name="goe_collection",
    embedding_function=bedrock_ef,  # type: ignore
)
web_vuln_collection = chroma_client.get_or_create_collection(
    name="web_vuln_atoms",
    embedding_function=bedrock_ef,  # type: ignore
)

COLLECTION_MAP = {
    "misconfig": misconfig_collection,
    "web_vulnerability": web_vuln_collection,
}


def _parse_atom_type(content: str) -> str:
    """Extract `type` from YAML frontmatter. Defaults to 'misconfig'."""
    if not content.startswith("---"):
        return "misconfig"
    try:
        end = content.index("---", 3)
        fm = yaml.safe_load(content[3:end]) or {}
        return fm.get("type", "misconfig")
    except (ValueError, yaml.YAMLError):
        return "misconfig"


def ingest_atoms(atoms_root: str = str(ATOMS_DIR)) -> None:
    """Ingest all atoms found under atoms_root (recursively) into ChromaDB.

    Routes each atom to goe_collection or web_vuln_atoms based on the
    `type` field in its YAML frontmatter:
      - type: web_vulnerability  → web_vuln_atoms
      - (anything else / absent) → goe_collection
    """
    atoms_root_path = Path(atoms_root)

    # Snapshot existing state of both collections for diff/prune
    existing_per_type: dict[str, dict[str, float]] = {}
    for type_key, collection in COLLECTION_MAP.items():
        existing_data = collection.get(include=["metadatas"])
        existing_files: dict[str, float] = {}
        if existing_data and existing_data["metadatas"]:
            for i, atom_id in enumerate(existing_data["ids"]):
                metadata = existing_data["metadatas"][i]
                existing_files[atom_id] = metadata.get("mtime", 0)
        existing_per_type[type_key] = existing_files

    # Accumulate upsert batches and track current IDs per collection
    to_upsert: dict[str, dict] = {
        k: {"docs": [], "metas": [], "ids": []} for k in COLLECTION_MAP
    }
    current_ids_per_type: dict[str, set] = {k: set() for k in COLLECTION_MAP}

    for filepath in sorted(atoms_root_path.rglob("*.md")):
        atom_name = filepath.stem
        current_mtime = filepath.stat().st_mtime
        content = filepath.read_text()
        atom_type = _parse_atom_type(content)

        if atom_type not in COLLECTION_MAP:
            print(f"  Warning: unknown type '{atom_type}' in {filepath.name}, skipping.")
            continue

        current_ids_per_type[atom_type].add(atom_name)
        existing_files = existing_per_type[atom_type]

        if atom_name not in existing_files or current_mtime > existing_files[atom_name]:
            to_upsert[atom_type]["docs"].append(content)
            to_upsert[atom_type]["metas"].append({
                "filename": atom_name,
                "mtime": current_mtime,
            })
            to_upsert[atom_type]["ids"].append(atom_name)

    # Upsert new/updated atoms and prune deleted ones, per collection
    for type_key, collection in COLLECTION_MAP.items():
        batch = to_upsert[type_key]
        if batch["ids"]:
            collection.upsert(
                ids=batch["ids"],
                documents=batch["docs"],
                metadatas=batch["metas"],
            )
            print(f"[{type_key}] Upserted {len(batch['ids'])} atoms into ChromaDB.")
        else:
            print(f"[{type_key}] No new or updated atoms.")

        ids_to_delete = [
            aid for aid in existing_per_type[type_key]
            if aid not in current_ids_per_type[type_key]
        ]
        if ids_to_delete:
            collection.delete(ids=ids_to_delete)
            print(f"[{type_key}] Deleted {len(ids_to_delete)} atoms no longer on disk.")


if __name__ == "__main__":
    ingest_atoms()
