import os
import json
import sqlite3
from typing import List, Dict, Any
from .base import BaseEmbeddingProvider
from .chunker import chunk_text
from .providers import PROVIDER_REGISTER
from dotenv import load_dotenv

load_dotenv()

class EmbeddingPipeline:
    def __init__(self, provider: BaseEmbeddingProvider = None, db_path: str = None):
        self.db_path = db_path or os.getenv("DATABASE_URL")
        if self.provider is None:
            assert os.getenv("EMBEDDING_PROVIDER") in list(PROVIDER_REGISTER.keys()), f"env var for EMBEDDING_PROVIDER must be one of: {list(PROVIDER_REGISTER.keys())}"
            self.provider = provider or PROVIDER_REGISTER[os.getenv("EMBEDDING_PROVIDER")](os.getenv("EMBEDDING_MODEL"))
        else:
            self.provider = provider
        
    def _get_db_connection(self): #TODO: Change for production / postgres database. 
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_unembedded_speeches(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Finds speeches that do not yet exist in the speech_embeddings table."""
        query = """
            SELECT s.speech_id, s.speech_text 
            FROM speeches s
            LEFT JOIN speech_embeddings e ON s.speech_id = e.speech_id
            WHERE e.speech_id IS NULL AND s.speech_text IS NOT NULL AND s.speech_text != ''
            LIMIT ?
        """
        with self._get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def run(self, batch_size: int = 100):
        speeches = self.get_unembedded_speeches(limit=batch_size)
        if not speeches:
            print("No new speeches to embed.")
            return

        print(f"Processing {len(speeches)} speeches...")
        
        chunks_to_embed = []
        metadata_payloads = []  # To keep track of which chunk belongs to which speech_id
        
        # 1. Step through speeches and chunk them
        for speech in speeches:
            chunks = chunk_text(speech['speech_text'], max_words=300, overlap=50)
            for idx, chunk in enumerate(chunks):
                chunks_to_embed.append(chunk)
                metadata_payloads.append({
                    "speech_id": speech['speech_id'],
                    "chunk_index": idx,
                    "chunk_text": chunk
                })
                
        if not chunks_to_embed:
            return

        # 2. Bulk generate embeddings via the selected provider
        print(f"Generating vectors for {len(chunks_to_embed)} text chunks using {self.provider.model_name}...")
        vectors = self.provider.embed_batch(chunks_to_embed)
        
        # 3. Write back to database
        insert_query = """
            INSERT INTO speech_embeddings (speech_id, chunk_index, chunk_text, embedding_vector, model_name)
            VALUES (?, ?, ?, ?, ?)
        """
        
        with self._get_db_connection() as conn:
            cursor = conn.cursor()
            prepare_data = []
            
            for meta, vector in zip(metadata_payloads, vectors):
                prepare_data.append((
                    meta['speech_id'],
                    meta['chunk_index'],
                    meta['chunk_text'],
                    json.dumps(vector), # Serialize vector float array to string for SQLite - #TODO Alter for vector database logic
                    self.provider.model_name
                ))
                
            cursor.executemany(insert_query, prepare_data)
            conn.commit()
            
        print(f"Successfully embedded and saved {len(prepare_data)} records.")