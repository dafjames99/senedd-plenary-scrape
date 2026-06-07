# run_embeddings.py

from src.embeddings.pipeline import EmbeddingPipeline
from src.embeddings.providers import SentenceTransformersProvider, OllamaProvider, OpenAiProvider

# Choose your provider strategy dynamically:

# provider = SentenceTransformersProvider(model_name="all-MiniLM-L6-v2")
# provider = OllamaProvider(model_name="nomic-embed-text")
# provider = FrontierApiProvider(model_name="text-embedding-3-small")

pipeline = EmbeddingPipeline() # DEFAULT AS PER .env

# Run an ingestion cycle
pipeline.run(batch_size=200)