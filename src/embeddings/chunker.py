import re
from typing import List

# Periods that are NOT sentence boundaries: titles, Welsh abbreviations, single initials
_PROTECT_ABBREVS = re.compile(
    r'\b(Mr|Mrs|Ms|Dr|Prof|Rev|Hon|St|No|vs|etc'
    r'|e\.e|h\.y|ac\.y\.b|A\.S|A\.C|U\.C\.B|Eg'
    r'|[A-Z])\.',
    re.IGNORECASE
)
_PROTECT_DECIMALS = re.compile(r'(\d+)\.(\d+)')
_SPLIT_SENTENCE = re.compile(r'(?<=[.!?])\s+')


def _tokenize_sentences(text: str) -> List[str]:
    # Mask periods that are not sentence-ending so the splitter ignores them
    protected = _PROTECT_ABBREVS.sub(lambda m: m.group().replace('.', '\x00'), text)
    protected = _PROTECT_DECIMALS.sub(lambda m: f"{m.group(1)}\x00{m.group(2)}", protected)
    raw = _SPLIT_SENTENCE.split(protected)
    return [s.replace('\x00', '.').strip() for s in raw if s.strip()]


def _sentences_for_overlap(sentences: List[str], overlap_words: int) -> List[str]:
    """Return the suffix of `sentences` covering at least `overlap_words` words."""
    accumulated = 0
    result = []
    for sentence in reversed(sentences):
        result.insert(0, sentence)
        accumulated += len(sentence.split())
        if accumulated >= overlap_words:
            break
    return result


def chunk_text(
    text: str,
    max_words: int = 300,
    overlap_words: int = 50,
    min_words: int = 20,
) -> List[str]:
    """
    Splits text into chunks by sentence boundaries, ensuring no chunk exceeds max_words.
    Each chunk boundary carries forward approximately overlap_words of context from the
    previous chunk. Trailing chunks smaller than min_words are merged into the last chunk.
    """
    if not text or not text.strip():
        return []

    sentences = _tokenize_sentences(text)

    if not sentences:
        sentences = [text.strip()]

    chunks = []
    current_chunk_sentences = []
    current_chunk_word_count = 0

    for sentence in sentences:
        sentence_word_count = len(sentence.split())

        # Guard rail: single sentence longer than max_words — emit as-is
        if sentence_word_count > max_words and not current_chunk_sentences:
            chunks.append(sentence)
            continue

        if current_chunk_word_count + sentence_word_count > max_words:
            if current_chunk_sentences:
                chunks.append(" ".join(current_chunk_sentences))

            overlap_sentences = _sentences_for_overlap(current_chunk_sentences, overlap_words)
            overlap_word_count = sum(len(s.split()) for s in overlap_sentences)
            current_chunk_sentences = overlap_sentences + [sentence]
            current_chunk_word_count = overlap_word_count + sentence_word_count
        else:
            current_chunk_sentences.append(sentence)
            current_chunk_word_count += sentence_word_count

    if current_chunk_sentences:
        final_text = " ".join(current_chunk_sentences)
        if chunks and len(final_text.split()) < min_words:
            chunks[-1] = chunks[-1] + " " + final_text
        else:
            chunks.append(final_text)

    return chunks
