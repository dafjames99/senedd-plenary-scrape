import json
import logging
from datetime import datetime
from sqlalchemy import select, create_engine
from sqlalchemy.orm import sessionmaker, Session
from typing import List

from src.db.db_schema import (
    Speech, SpeechEmbedding
)
from src.db.settings import settings
from .base import BaseEmbeddingProvider
from .chunker import chunk_text
from .providers import PROVIDER_REGISTER
from .config import MODEL_METADATA_REGISTRY

logger = logging.getLogger(__name__)
class EmbeddingPipeline:
    def __init__(self, provider: BaseEmbeddingProvider = None, db_path: str = None):
        db_path = db_path or settings.database_url
        self.engine = create_engine(db_path)
        self.SessionLocal = sessionmaker(bind=self.engine)
        
        self.provider = provider or PROVIDER_REGISTER[settings.embedding_provider](settings.embedding_model)
        self.model_meta = MODEL_METADATA_REGISTRY.get(self.provider.model_name, None)
        
        if self.model_meta is None:
            raise ValueError(f"{self.provider} & {self.provider.model_name} are not valid.")

    def get_unembedded_speeches(self, session: Session, limit: int = 100) -> List[Speech]:
        """
        Finds speeches that do not yet exist in the speech_embeddings table 
        SPECIFICALLY for the currently active provider model name.
        """
        stmt = (
            select(Speech)
            .outerjoin(
                SpeechEmbedding,
                (SpeechEmbedding.source_type == "speech") &
                (SpeechEmbedding.source_id == Speech.speech_id) &
                (SpeechEmbedding.model_name == self.provider.model_name)
            )
            .where(SpeechEmbedding.embedding_id == None)
            .where(Speech.speech_text != None)
            .where(Speech.speech_text != "")
            .limit(limit)
        )
        result = session.execute(stmt)
        return list(result.scalars().all())

    def purge_active_model_embeddings(self):
        """Deletes all existing database rows associated with the active model."""
        with self.SessionLocal() as session:
            with session.begin():
                logger.warning(
                    "Purging all database vectors matching model target: %s", 
                    self.provider.model_name
                )
                session.query(SpeechEmbedding).filter(
                    SpeechEmbedding.model_name == self.provider.model_name
                ).delete(synchronize_session=False)

    def run(self, batch_size: int = 100):
        """Runs one batch cycle of fetching, chunking, embedding, and saving."""
        # Use context manager to handle session scope and transaction boundaries
        with self.SessionLocal() as session:
            max_words = self.model_meta["max_chunk_words"]
            doc_prefix = self.model_meta["doc_prefix"]
            
            speeches = self.get_unembedded_speeches(session, limit=batch_size)
            
            if not speeches:
                logger.info("No new speeches to embed. Hibernating")
                return

            logger.info("Processing %d speeches from database.", len(speeches))
            
            chunks_to_embed = []
            metadata_payloads = []
            
            # 1. Chunk texts using your sliding window utility
            MIN_SPEECH_WORDS = 10
            for speech in speeches:
                if len(speech.speech_text.split()) < MIN_SPEECH_WORDS:
                    logger.debug(
                        "Skipping speech %d (%r): below %d-word threshold.",
                        speech.speech_id, speech.speaker_name, MIN_SPEECH_WORDS
                    )
                    continue
                chunks = chunk_text(speech.speech_text, max_words=max_words)
                speaker_prefix = f"{speech.speaker_name}: "
                for idx, chunk in enumerate(chunks):
                    contextualized = speaker_prefix + chunk
                    formatted_chunk = f"{doc_prefix}{contextualized}" if doc_prefix else contextualized
                    chunks_to_embed.append(formatted_chunk)
                    metadata_payloads.append({
                        "speech_id": speech.speech_id,
                        "chunk_index": idx,
                        "chunk_text": contextualized,
                    })
                    
            if not chunks_to_embed:
                return

            # 2. Compute vectors via your injected strategy provider
            logger.info("Generating vectors for %d chunks using Model=%s", len(chunks_to_embed), self.provider.model_name)
            vectors = self.provider.embed_batch(chunks_to_embed)
            
            # 3. Build SpeechEmbedding objects and commit them to the database
            embedding_objects = []
            for meta, vector in zip(metadata_payloads, vectors):
                embedding_obj = SpeechEmbedding(
                    source_type="speech",
                    source_id=meta["speech_id"],
                    speech_id=meta["speech_id"],  # legacy column, kept this release
                    chunk_index=meta["chunk_index"],
                    chunk_text=meta["chunk_text"],
                    embedding_vector=vector,
                    model_name=self.provider.model_name,
                    created_at=datetime.now()
                )
                embedding_objects.append(embedding_obj)
                
            try:
                session.add_all(embedding_objects)
                session.commit()
                logger.info("Successfully embedded and saved %d records.", len(embedding_objects))
            except Exception as e:
                session.rollback()
                logger.error("Error during database insertion, rolling back transaction. Details: %s", e)
                raise e