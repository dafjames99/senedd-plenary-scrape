"""Text cleaning and row classification logic."""
import html
import re
from bs4 import BeautifulSoup
from typing import Optional, Tuple


def double_unescape_html(text: str):
    return html.unescape(html.unescape(text))


def remove_html_tags(text: Optional[str]) -> Optional[str]:
    """Remove HTML tags from text."""
    return BeautifulSoup(text, "html.parser").get_text("")

    
def normalize_and_clean(text: Optional[str]) -> Optional[str]:
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+\.\s*", ". ", text)
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r"\s+\?", "?", text)
    text = re.sub(r"\s+!", "!", text)
    
    text = text.strip()
    return text


def clean_contribution_verbatim(text: Optional[str]) -> Optional[str]:
    """
    Full cleaning pipeline for contribution_verbatim:
    1. Double HTML unescape
    2. Remove HTML tags
    3. Normalize whitespace
    4. Return None if empty
    """
    if not isinstance(text, str) or not text:
        return None
    
    text = double_unescape_html(text)
    text = remove_html_tags(text)
    text = normalize_and_clean(text)
    
    return text if text else None


def classify_contribution(row: dict) -> Tuple[str, str]:
    """
    Classify a contribution row as speech/procedural/noise.
    
    Returns: (row_type, classification_reason)
    
    Classification rules:
    - PROCEDURAL: Llywydd title OR contribution_type in {I, B}
    - NOISE: no Member_Id AND no text
    - ORAL_QUESTION: contribution_type in {O}
    - SPEECH: Member_Id present AND not Llywydd AND has text
    """
    
    member_id = row.get('Member_Id')
    member_job_title = row.get('Member_job_title_English', '')
    contribution_type = row.get('contribution_type', '')
    text = row.get('contribution_verbatim') or row.get('contribution_translated')
    
    # Check for Llywydd (procedural)
    if member_job_title and 'Llywydd' in member_job_title:
        return ('procedural', 'Llywydd speaker')
    
    # Check for procedural contribution types
    if contribution_type in ['I', 'B']:
        return ('procedural', f'Contribution type {contribution_type}')
    
    if contribution_type == 'O':
        verbatim = row.get('contribution_verbatim', '')
        if not verbatim:
            return ('oral-question', f'Contribution type {contribution_type}')
        else:
            if "TQ" in verbatim:
                return ('topical-question', f'Contribution type {contribution_type}')
            elif 'OQ' in verbatim:
                return ('oral-question', f'Contribution type {contribution_type}')
    # Check if row is noise (no speaker, no text)
    if not member_id and not text:
        return ('noise', 'No speaker and no text')
    
    # Check if potential speech has substantive content
    if member_id and text:
        return ('speech', 'Valid speech')
    
    if contribution_type == 'V':
        return ('procedural', f'Contribution type {contribution_type}')
    # Fallback: noise
    return ('noise', 'Insufficient content')


def parse_oral_question_meta(text: str) -> Tuple[Optional[int], Optional[str], str]:
    """Extracts question number and ID from a single string instance,
    returning the cleaned text alongside the metadata.
    
    Returns:
        Tuple[Optional[int], Optional[str], str]: (question_number, question_id, clean_text)
    """
    if not text:
        return None, None, ""

    text_stripped = text.strip().strip('"\'')

    pattern = (
        r"^\s*(?P<question_num>\d+)\.\s*(?P<clean_text>.*?)\s*(?P<question_id>(?:OQ|TQ)\d*)\s*$"
    )
    
    match = re.match(pattern, text_stripped)
    
    if match:
        extracted = match.groupdict()
        return (
            int(extracted['question_num']), 
            extracted['question_id'], 
            extracted['clean_text'].strip()
        )
    
    return None, None, text.strip()