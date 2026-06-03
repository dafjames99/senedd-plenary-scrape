from typing import Dict, Any

# Centralized registry of model constraints and architectural requirements
MODEL_METADATA_REGISTRY: Dict[str, Dict[str, Any]] = {
    "ollama/embeddinggemma:300m": {
        "max_context_tokens": 2048,
        "max_chunk_words": 1200,          # Safe translation from ~2048 tokens
        "dimensions": 768,
        "requires_prefixes": True,
        "doc_prefix": "title: none | text: ",
        "query_prefix": "task: search result | query: "
    },
    "openai/text-embedding-3-small": {
        "max_context_tokens": 8191,
        "max_chunk_words": 4000,
        "dimensions": 1536,
        "requires_prefixes": False,
        "doc_prefix": "",
        "query_prefix": ""
    },
    "sentence-transformers/all-MiniLM-L6-v2": {
        "max_context_tokens": 512,
        "max_chunk_words": 300,
        "dimensions": 384,
        "requires_prefixes": False,
        "doc_prefix": "",
        "query_prefix": ""
    }
}