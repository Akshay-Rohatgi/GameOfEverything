"""Query ChromaDB atom collections for manual RAG retrieval testing.

Usage:
    python scripts/query.py "search query"
    python scripts/query.py "sql injection login" --collection web_vuln_atoms
    python scripts/query.py "samba share" --n 5
    python scripts/query.py "template injection" --collection web_vuln_atoms --n 1
"""

import os
import sys
import argparse
import tomllib
from pathlib import Path
import chromadb
import boto3
from chromadb.utils.embedding_functions import AmazonBedrockEmbeddingFunction

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CHROMA_DB_PATH = PROJECT_DIR / "src/game_of_everything" / "chroma_db"

COLLECTION_NAMES = {
    "atoms": "goe_collection",
    "web_vuln_atoms": "web_vuln_atoms",
}

# Read AWS credentials from goe.toml (same as rag_gen.py), with env var override.
toml_path = PROJECT_DIR / "goe.toml"
_aws_cfg: dict = {}
if toml_path.exists():
    with open(toml_path, "rb") as _f:
        _aws_cfg = tomllib.load(_f).get("aws", {})

_session_kwargs: dict = {
    "region_name": os.getenv("AWS_REGION", _aws_cfg.get("region", "us-east-1"))
}
_key_id = os.getenv("AWS_ACCESS_KEY_ID") or _aws_cfg.get("access_key_id", "")
_secret = os.getenv("AWS_SECRET_ACCESS_KEY") or _aws_cfg.get("secret_access_key", "")
if _key_id and _secret:
    _session_kwargs["aws_access_key_id"] = _key_id
    _session_kwargs["aws_secret_access_key"] = _secret

aws_session = boto3.Session(**_session_kwargs)

bedrock_ef = AmazonBedrockEmbeddingFunction(
    session=aws_session,
    model_name="amazon.titan-embed-text-v2:0",
)

chroma_client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))


def query_collection(search_text: str, collection_key: str = "atoms", n_results: int = 3) -> None:
    chroma_name = COLLECTION_NAMES.get(collection_key)
    if not chroma_name:
        print(f"Unknown collection '{collection_key}'. Valid: {list(COLLECTION_NAMES.keys())}")
        sys.exit(1)

    collection = chroma_client.get_collection(
        name=chroma_name,
        embedding_function=bedrock_ef,  # type: ignore
    )

    print(f"Collection : {collection_key} ({chroma_name})")
    print(f"Query      : '{search_text}'")
    print(f"Top        : {n_results}\n")

    results = collection.query(
        query_texts=[search_text],
        n_results=n_results,
    )

    if not results["documents"] or not results["documents"][0]:
        print("No matches found.")
        return

    for i in range(len(results["documents"][0])):
        doc_id = results["ids"][0][i]
        distance = (
            results["distances"][0][i]
            if results.get("distances")
            else "N/A"
        )
        print(f"--- MATCH {i + 1} ---")
        print(f"ID       : {doc_id}")
        print(f"Distance : {distance:.4f}" if isinstance(distance, float) else f"Distance : {distance}")
        snippet = results["documents"][0][i][:300].replace("\n", " ")
        print(f"Snippet  : {snippet}...\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Query ChromaDB atom collections.")
    parser.add_argument("query", nargs="?", default="anonymous samba share with no password",
                        help="Search query text")
    parser.add_argument("--collection", "-c", default="atoms",
                        choices=list(COLLECTION_NAMES.keys()),
                        help="Collection to search (default: atoms)")
    parser.add_argument("--n", "-n", type=int, default=3,
                        help="Number of results to return (default: 3)")
    args = parser.parse_args()

    query_collection(args.query, collection_key=args.collection, n_results=args.n)
