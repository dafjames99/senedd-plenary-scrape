from typing import List, Tuple

def chunk_text(text: str, max_words: int = 300, overlap: int = 50) -> List[str]:
    """
    Splits text into chunks of max_words with a specified word overlap.
    """
    if not text:
        return []
        
    words = text.split()
    if len(words) <= max_words:
        return [text]
        
    chunks = []
    start = 0
    while start < len(words):
        end = start + max_words
        chunk_words = words[start:end]
        chunks.append(" ".join(chunk_words))
        start += (max_words - overlap)
        
    return chunks