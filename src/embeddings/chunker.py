import re
from typing import List

def chunk_text(text: str, max_words: int = 300) -> List[str]:
    """
    Splits text into chunks by sentence boundaries, ensuring no chunk exceeds max_words.
    The last sentence of each chunk acts as the overlapping start for the next chunk.
    """
    if not text or not text.strip():
        return []

    # 1. Split text into sentences while preserving trailing spaces/punctuation
    # This regex splits at ., !, or ? followed by whitespace, keeping the punctuation attached
    sentence_endings = re.compile(r'([^.!?]+[.!?]+(?:\s+|$))')
    raw_sentences = sentence_endings.split(text)
    
    # Reassemble pieces (filter out empty strings caused by splitting)
    sentences = [s.strip() for s in raw_sentences if s and s.strip()]
    
    # Fallback: If no sentence punctuation is found at all, handle it as one big sentence
    if not sentences:
        sentences = [text.strip()]

    chunks = []
    current_chunk_sentences = []
    current_chunk_word_count = 0

    for idx, sentence in enumerate(sentences):
        sentence_word_count = len(sentence.split())
        
        # Guard rail: If a single sentence is somehow longer than the max_words limit on its own, 
        # we have no choice but to push it in or truncate it. We'll push it in to avoid losing data.
        if sentence_word_count > max_words and not current_chunk_sentences:
            chunks.append(sentence)
            continue

        # Check if adding this sentence blows past our max word target
        if current_chunk_word_count + sentence_word_count > max_words:
            # Commit the current chunk
            if current_chunk_sentences:
                chunks.append(" ".join(current_chunk_sentences))
            
            # Start the next chunk using the *last sentence* of the committed chunk as overlap
            if current_chunk_sentences:
                overlap_sentence = current_chunk_sentences[-1]
                current_chunk_sentences = [overlap_sentence, sentence]
                current_chunk_word_count = len(overlap_sentence.split()) + sentence_word_count
            else:
                current_chunk_sentences = [sentence]
                current_chunk_word_count = sentence_word_count
        else:
            # Add sentence to the active working chunk
            current_chunk_sentences.append(sentence)
            current_chunk_word_count += sentence_word_count

    # Catch the remaining trailing sentences for the final chunk
    if current_chunk_sentences:
        chunks.append(" ".join(current_chunk_sentences))

    return chunks