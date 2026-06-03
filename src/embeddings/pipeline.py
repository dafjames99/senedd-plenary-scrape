from datetime import datetime
from sqlalchemy import select, create_engine
from sqlalchemy.orm import sessionmaker, Session
import os
import json
import sqlite3
from typing import List, Dict, Any
from src.db_schema import (
    Speech, SpeechEmbedding
)
from .config import MODEL_METADATA_REGISTRY
from .base import BaseEmbeddingProvider
from .chunker import chunk_text
from .providers import PROVIDER_REGISTER
from dotenv import load_dotenv

load_dotenv()
class EmbeddingPipeline:
    def __init__(self, provider: BaseEmbeddingProvider = None, db_path: str = None):
        db_path = db_path or os.getenv("DATABASE_URL")
        self.engine = create_engine(db_path)
        self.SessionLocal = sessionmaker(bind=self.engine)
        
        if provider is None:
            assert os.getenv("EMBEDDING_PROVIDER") in list(PROVIDER_REGISTER.keys()), f"env var for EMBEDDING_PROVIDER must be one of: {list(PROVIDER_REGISTER.keys())}"
            self.provider = provider or PROVIDER_REGISTER[os.getenv("EMBEDDING_PROVIDER")](os.getenv("EMBEDDING_MODEL"))
        else:
            self.provider = provider
        self.model_meta = MODEL_METADATA_REGISTRY.get(self.provider.model_name, None)
        
        if self.model_meta is None:
            raise ValueError(f"{self.provider} & {self.provider.model_name} are not valid.")

    def get_unembedded_speeches(self, session: Session, limit: int = 100) -> List[Speech]:
        """Finds speeches that do not yet exist in the speech_embeddings table."""
        stmt = (
            select(Speech)
            .outerjoin(Speech.embeddings)
            .where(SpeechEmbedding.speech_id == None)
            .where(Speech.speech_text != None)
            .where(Speech.speech_text != "")
            .limit(limit)
        )
        result = session.execute(stmt)
        return list(result.scalars().all())

    def run(self, batch_size: int = 100):
        """Runs one batch cycle of fetching, chunking, embedding, and saving."""
        # Use context manager to handle session scope and transaction boundaries
        with self.SessionLocal() as session:
            max_words = self.model_meta["max_chunk_words"]
            doc_prefix = self.model_meta["doc_prefix"]
            
            speeches = self.get_unembedded_speeches(session, limit=batch_size)
            
            if not speeches:
                print("✓ No new speeches to embed.")
                return

            print(f"Processing {len(speeches)} speeches from database...")
            
            chunks_to_embed = []
            metadata_payloads = []
            
            # 1. Chunk texts using your sliding window utility
            for speech in speeches:
                chunks = chunk_text(speech.speech_text, max_words=max_words)
                for idx, chunk in enumerate(chunks):
                    formatted_chunk = f"{doc_prefix}{chunk}" if doc_prefix else chunk
                    chunks_to_embed.append(formatted_chunk)
                    metadata_payloads.append({
                        "speech_id": speech.speech_id,
                        "chunk_index": idx,
                        "chunk_text": chunk
                    })
                    
            if not chunks_to_embed:
                return

            # 2. Compute vectors via your injected strategy provider
            print(f"Generating vectors for {len(chunks_to_embed)} chunks using {self.provider.model_name}...")
            vectors = self.provider.embed_batch(chunks_to_embed)
            
            # 3. Build SpeechEmbedding objects and commit them to the database
            embedding_objects = []
            for meta, vector in zip(metadata_payloads, vectors):
                embedding_obj = SpeechEmbedding(
                    speech_id=meta["speech_id"],
                    chunk_index=meta["chunk_index"],
                    chunk_text=meta["chunk_text"],
                    embedding_vector=json.dumps(vector),  # Serialized JSON array string for SQLite
                    model_name=self.provider.model_name,
                    created_at=datetime.now()
                )
                embedding_objects.append(embedding_obj)
                
            try:
                session.add_all(embedding_objects)
                session.commit()
                print(f"✓ Successfully embedded and saved {len(embedding_objects)} records.")
            except Exception as e:
                session.rollback()
                print(f"❌ Error during database insertion, rolling back transaction. Details: {e}")
                raise e