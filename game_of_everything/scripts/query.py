import os
from pathlib import Path
import chromadb 
import boto3
from dotenv import load_dotenv
from chromadb.utils.embedding_functions import AmazonBedrockEmbeddingFunction

load_dotenv()

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
ATOMS_DIR = PROJECT_DIR / "atoms"
CHROMA_DB_PATH = PROJECT_DIR / "src/game_of_everything" / "chroma_db"

# 1. Authenticate with AWS using the same credentials
aws_session = boto3.Session( 
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", ""),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
    region_name=os.getenv("AWS_REGION", "us-east-1"),
)

# 2. Reinitialize the Bedrock embedding function
bedrock_ef = AmazonBedrockEmbeddingFunction( 
    session=aws_session,
    model_name="amazon.titan-embed-text-v2:0",
)

# 3. Connect to your local ChromaDB and fetch the collection
chroma_client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))
collection = chroma_client.get_collection(
    name="goe_collection", 
    embedding_function=bedrock_ef # type: ignore
)

def query_database(search_text: str, n_results: int = 3):
    """
    Queries the ChromaDB collection for the most relevant documents.
    """
    print(f"Searching for: '{search_text}'\n")
    
    # The .query() method automatically embeds the text and performs a similarity search
    results = collection.query(
        query_texts=[search_text],
        n_results=n_results
    )
    
    # 4. Parse and display the output
    if not results['documents'] or not results['documents'][0]:
        print("No matches found.")
        return

    for i in range(len(results['documents'][0])):
        doc_id = results['ids'][0][i]
        # ChromaDB returns a distance score (lower means the vectors are closer/more similar)
        distance = results['distances'][0][i] if 'distances' in results and results['distances'] else "N/A"
        metadata = results['metadatas'][0][i] if 'metadatas' in results and results['metadatas'] else "N/A"
        
        print(f"--- MATCH {i+1} ---")
        print(f"ID: {doc_id}")
        print(f"Distance Score: {distance}")
        print(f"Metadata: {metadata}")
        
        # Print the first 250 characters of the markdown file to verify content
        snippet = results['documents'][0][i][:250].replace('\n', ' ')
        print(f"Content Snippet: {snippet}...\n")

if __name__ == "__main__":
    # Test your retrieval pipeline!
    query_database("anonymous samba share with no password")