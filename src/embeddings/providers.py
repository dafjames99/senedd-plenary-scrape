import os
from tqdm import tqdm
from dotenv import load_dotenv
from typing import List
from .base import BaseEmbeddingProvider
load_dotenv()
class SentenceTransformersProvider(BaseEmbeddingProvider):
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        # Deferred import so you don't need torch/sentence-transformers installed to run other providers
        from sentence_transformers import SentenceTransformer
        self.key = os.getenv("HF_TOKEN")
        self._model_name = model_name
        self.model = SentenceTransformer(model_name, token = self.key)
        
    @property
    def model_name(self) -> str:
        return f"sentence-transformers/{self._model_name}"
        
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        embeddings = self.model.encode(texts, convert_to_numpy=True)
        return embeddings.tolist()


class OllamaProvider(BaseEmbeddingProvider):
    def __init__(self, model_name: str = None, base_url: str = None):
        import ollama
        if base_url is None:
            base_url = os.getenv("OLLAMA_URL")
        self._model_name = model_name or os.getenv("EMBEDDING_MODEL")
        self.client = ollama.Client(host=base_url)
        
    @property
    def model_name(self) -> str:
        return f"ollama/{self._model_name}"
        
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        # Ollama handles batches sequentially or natively depending on the model/server config
        results = []
        for text in tqdm(texts, "Embedding texts ...", total = len(texts)):
            response = self.client.embed(model=self._model_name, input=text)
            results.append(response['embeddings'])
        return results


class OpenAiProvider(BaseEmbeddingProvider):
    def __init__(self, model_name: str = "text-embedding-3-small"):
        from openai import OpenAI
        self._model_name = model_name
        # Assumes OPENAI_API_KEY is set in your environment/.env file
        self.client = OpenAI()
        
    @property
    def model_name(self) -> str:
        return f"openai/{self._model_name}"
        
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        response = self.client.embeddings.create(
            input=texts,
            model=self._model_name
        )
        return [data.embedding for data in response.data]
    
PROVIDER_REGISTER = {
    "sentence-transformer": SentenceTransformersProvider,
    "ollama": OllamaProvider,
    "openai": OpenAiProvider
}