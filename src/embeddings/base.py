from abc import ABC, abstractmethod
from typing import List

class BaseEmbeddingProvider(ABC):
    """Abstract base class for all embedding providers."""
    
    @abstractmethod
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Takes a list of string chunks and returns a list of vector embeddings."""
        pass
    
    @property
    @abstractmethod
    def model_name(self) -> str:
        """Returns the identifier string of the model being used."""
        pass