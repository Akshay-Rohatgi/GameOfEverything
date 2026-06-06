"""Semantic atom search for the v2 planner — thin wrapper over ChromaDB/Bedrock.

Deliberately no crewAI dependency. Mirrors the query logic in
src/game_of_everything/tools/search_atoms_tool.py but returns plain dicts.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_CHROMA_DB_PATH = Path(__file__).resolve().parent.parent.parent / "src" / "game_of_everything" / "chroma_db"
_WEB_ATOMS_COLLECTION = "web_vuln_atoms"


@lru_cache(maxsize=1)
def _get_collection():
    import boto3
    import chromadb
    from chromadb.utils.embedding_functions import AmazonBedrockEmbeddingFunction

    from goe.config import GoEConfig

    cfg = GoEConfig.get()
    session = boto3.Session(
        aws_access_key_id=cfg.aws_access_key_id or None,
        aws_secret_access_key=cfg.aws_secret_access_key or None,
        region_name=cfg.aws_region,
    )
    ef = AmazonBedrockEmbeddingFunction(
        session=session,
        model_name="amazon.titan-embed-text-v2:0",
    )
    client = chromadb.PersistentClient(path=str(_CHROMA_DB_PATH))
    return client.get_collection(name=_WEB_ATOMS_COLLECTION, embedding_function=ef)


def search_atoms(query: str, n_results: int = 3) -> list[dict]:
    """Return top-n web vulnerability atoms matching the query.

    Each result: {"id": atom_id, "content": atom_markdown}
    Falls back to empty list if ChromaDB is unavailable.
    """
    try:
        collection = _get_collection()
        results = collection.query(query_texts=[query], n_results=n_results)
        if not results["documents"] or not results["documents"][0]:
            return []
        return [
            {"id": results["ids"][0][i], "content": results["documents"][0][i]}
            for i in range(len(results["documents"][0]))
        ]
    except Exception:
        return []


def atom_ids_for_query(query: str, n_results: int = 3) -> list[str]:
    """Return just the atom IDs for a query — fast path for prompt assembly."""
    return [r["id"] for r in search_atoms(query, n_results=n_results)]
