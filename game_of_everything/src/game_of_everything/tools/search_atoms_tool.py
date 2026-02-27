import os
import boto3
import chromadb
from dotenv import load_dotenv
from pathlib import Path
from typing import Type
from pydantic import BaseModel, Field
from crewai.tools import BaseTool
from chromadb.utils.embedding_functions import AmazonBedrockEmbeddingFunction

load_dotenv()

SCRIPT_DIR = Path(__file__).parent.parent.parent
PROJECT_DIR = SCRIPT_DIR.parent
ATOMS_DIR = PROJECT_DIR / "atoms"
CHROMA_DB_PATH = PROJECT_DIR / "src/game_of_everything" / "chroma_db"

class SearchAtomsInput(BaseModel):
    query: str = Field(
        ...,
        description="A natural language description of the vulnerability to search for.",
    )
    n_results: int = Field(
        default=3,
        description="Number of results to return. Use 1 for best-match-only queries.",
    )

class SearchAtomsTool(BaseTool):
    # FIX 1: Use snake_case for the name. It prevents string parsing errors.
    name: str = "search_vulnerability_atoms"
    description: str = (
        "Search the database for specific vulnerability configurations. "
        "You must pass a search string to the 'query' parameter."
    )
    args_schema: Type[BaseModel] = SearchAtomsInput

    def _run(self, query: str, n_results: int = 3) -> str:
        search_string = query.strip()

        if not search_string:
            return (
                "Error: You must provide a non-empty 'query' argument. "
                "Example Action Input: {\"query\": \"samba share\"}"
            )

        # 1. Authenticate with AWS
        aws_session = boto3.Session(
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", ""),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
            region_name=os.getenv("AWS_REGION", "us-east-1"),
        )

        # 2. Initialize the Bedrock embedding function
        bedrock_ef = AmazonBedrockEmbeddingFunction(
            session=aws_session,
            model_name="amazon.titan-embed-text-v2:0",
        )

        # 3. Connect to the local ChromaDB
        chroma_client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))
        collection = chroma_client.get_collection(
            name="goe_collection",
            embedding_function=bedrock_ef,  # type: ignore
        )

        # 4. Execute the search using the safely extracted search_string
        results = collection.query(
            query_texts=[search_string],
            n_results=n_results,
        )

        if not results["documents"] or not results["documents"][0]:
            return f"No relevant vulnerability Atoms found for query: '{search_string}'"

        formatted_output = f"Results for '{search_string}':\n\n"
        for i in range(len(results["documents"][0])):
            atom_name = results["ids"][0][i]
            content = results["documents"][0][i]
            formatted_output += f"--- ATOM: {atom_name} ---\n{content}\n\n"

        return formatted_output