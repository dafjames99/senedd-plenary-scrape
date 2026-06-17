"""Retrieval service layer.

Shared by the CLI (`scripts/query_speeches.py`) and, in later phases, the MCP
server. Centralises semantic search and structured lookups so query-time prefix
symmetry and citation metadata live in exactly one place.
"""
from src.search.service import SearchResult, semantic_search

__all__ = ["SearchResult", "semantic_search"]
