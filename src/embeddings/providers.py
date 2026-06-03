import os
from typing import List
from .base import BaseEmbeddingProvider

class SentenceTransformersProvider(BaseEmbeddingProvider):
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        # Deferred import so you don't need torch/sentence-transformers installed to run other providers
        from sentence_transformers import SentenceTransformer
        self._model_name = model_name
        self.model = SentenceTransformer(model_name)
        
    @property
    def model_name(self) -> str:
        return f"sentence-transformers/{self._model_name}"
        
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        embeddings = self.model.encode(texts, convert_to_numpy=True)
        return embeddings.tolist()


class OllamaProvider(BaseEmbeddingProvider):
    def __init__(self, model_name: str = "nomic-embed-text", base_url: str = "http://localhost:11434"):
        import ollama
        self._model_name = model_name
        self.client = ollama.Client(host=base_url)
        
    @property
    def model_name(self) -> str:
        return f"ollama/{self._model_name}"
        
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        # Ollama handles batches sequentially or natively depending on the model/server config
        results = []
        for text in texts:
            response = self.client.embeddings(model=self._model_name, prompt=text)
            results.append(response['embedding'])
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