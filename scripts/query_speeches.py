"""Script to execute semantic vector proximity queries against Senedd speech embeddings."""
import argparse
from sqlalchemy import text
import sys
from pathlib import Path
import logging

# Automatically resolve root path boundaries for module resolution
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src import setup_logging, settings
from src.db.pipeline import SeneddPipeline
from src.embeddings.providers import PROVIDER_REGISTER

logger = logging.getLogger(__name__)


def semantic_search(query_text: str, top_k: int = 5, provider_string: str = None, model_string: str = None):
    """
    Transforms plain text into a vector and targets the HNSW index 
    to retrieve the closest semantic speeches.
    """
    logger.info(f"[*] Initializing semantic match sequence for: '{query_text}'")
    
    if not provider_string:
        provider_string = settings.embedding_provider
    if not model_string:
        model_string = settings.embedding_model
    
    provider = PROVIDER_REGISTER.get(provider_string)(model_string)
    query_vector = provider.embed_batch([query_text])[0]
    
    pipeline = SeneddPipeline(settings.database_url)
    
    raw_sql = text("""
        SELECT 
            speech_id,
            embedding_vector <=> :query_embedding AS cosine_distance,
            chunk_text
        FROM speech_embeddings
        ORDER BY cosine_distance ASC
        LIMIT :limit;
    """)
    
    with pipeline.SessionLocal() as session:
        result = session.execute(
            raw_sql, 
            {
                "query_embedding": str(query_vector), # pgvector handles stringified float arrays smoothly
                "limit": top_k
            }
        )
        
        matches = result.fetchall()
        
    # 4. Display the Results
    print("\n" + "="*80)
    print(f"SEMANTIC SEARCH RESULTS FOR: '{query_text}'")
    print("="*80)
    
    if not matches:
        print("[-] No matching semantic blocks found.")
        return
        
    for i, row in enumerate(matches, 1):
        # Convert distance to a human-readable similarity score percentage
        similarity_score = (1 - row.cosine_distance) * 100
        
        print(f"\n[{i}] SPEECH ID: {row.speech_id} | Match Confidence: {similarity_score:.2f}%")
        print(f"    Excerpt: {row.chunk_text[:180].strip()}...")
        print("-" * 60)


if __name__ == "__main__":
    setup_logging()
    
    parser = argparse.ArgumentParser(description="Semantic Vector Proximity Query CLI UI")
    parser.add_argument("query", type=str, help="The semantic text string context to search for (e.g. 'climate change')")
    parser.add_argument("--limit", type=int, default=5, help="Number of high-ranking elements to return.")
    args = parser.parse_args()
    
    semantic_search(args.query, top_k=args.limit)