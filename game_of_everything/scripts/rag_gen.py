import os
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

# print(f"Script directory: {SCRIPT_DIR}")
# print(f"Project directory: {PROJECT_DIR}")
# print(f"Atoms directory: {ATOMS_DIR}")
# print(f"ChromaDB path: {CHROMA_DB_PATH}")


aws_session = boto3.Session( # aws session for auth w/ bedrock
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", ""),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
    region_name=os.getenv("AWS_REGION", "us-east-1"),
)

bedrock_ef = AmazonBedrockEmbeddingFunction( # embedding function for chromadb that uses bedrock under the hood
    session=aws_session,
    model_name="amazon.titan-embed-text-v2:0",
)

chroma_client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))
collection = chroma_client.get_or_create_collection(
    name="goe_collection", 
    embedding_function=bedrock_ef # type: ignore
)

def ingest_atoms(atoms_directory=str(ATOMS_DIR)):
    """
    Ingest atoms from the specified directory into ChromaDB.
    
    1. First retrieve all existing atom IDs from the collection to avoid duplicates.
    2. Only upsert files that are new or have been modified.
    """
    existing_data = collection.get(include=["metadatas"]) 

    # map existing atom IDs to their last modified timestamps
    existing_files = {}
    if existing_data and existing_data["metadatas"]:
        for i, atom_id in enumerate(existing_data["ids"]):
            metadata = existing_data["metadatas"][i]
            existing_files[atom_id] = metadata.get("mtime", 0)

    documents_to_insert = []
    metadatas_to_upsert = []
    ids_to_upsert = []
    current_disk_ids = []

    for filename in os.listdir(atoms_directory):
        if filename.endswith(".md"):
            filepath = os.path.join(atoms_directory, filename)
            atom_name = filename.replace(".md", "")
            current_disk_ids.append(atom_name)

            current_mtime = os.path.getmtime(filepath)

            if atom_name not in existing_files or current_mtime > existing_files[atom_name]:
                content = None
                with open(filepath, "r") as f:
                    content = f.read()

                if content:
                    documents_to_insert.append(content)
                    metadatas_to_upsert.append({
                        "filename": atom_name, 
                        "mtime": current_mtime
                    })
                    ids_to_upsert.append(atom_name)
                
    # --- MOVED OUTSIDE THE FOR LOOP ---
    if ids_to_upsert:
        collection.upsert(
            ids=ids_to_upsert,
            documents=documents_to_insert,
            metadatas=metadatas_to_upsert
        )
        print(f"Upserted {len(ids_to_upsert)} atoms into ChromaDB.")
    else:
        print("No new or updated atoms to upsert.")
    
    ids_to_delete = [atom_id for atom_id in existing_files if atom_id not in current_disk_ids]
    if ids_to_delete:
        collection.delete(ids=ids_to_delete)
        print(f"Deleted {len(ids_to_delete)} atoms from ChromaDB that no longer exist on disk.")

if __name__ == "__main__":
    ingest_atoms()