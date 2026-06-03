"""Text cleaning and row classification logic."""
import html
import re
from bs4 import BeautifulSoup
from typing import Optional


def clean_transcript(text: Optional[str]) -> Optional[str]:
    """
    Clean transcript text: double HTML unescape, normalize whitespace.
    Returns None if text becomes empty after cleaning.
    """
    if not isinstance(text, str) or not text:
        return None
    
    # Double unescape HTML entities
    text = html.unescape(html.unescape(text))
    
    # Replace non-breaking space with regular space
    text = text.replace("\xa0", " ")
    
    # Normalize multiple spaces to single space
    text = re.sub(r"\s+", " ", text)
    
    # Fix spacing before punctuation
    text = re.sub(r"\s+\.\s*", ". ", text)
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r"\s+\?", "?", text)
    text = re.sub(r"\s+!", "!", text)
    
    text = text.strip()
    return text if text else None


def remove_html_tags(text: Optional[str]) -> Optional[str]:
    """Remove HTML tags from text."""
    if not isinstance(text, str) or not text:
        return None
    
    text = BeautifulSoup(text, "html.parser").get_text("")
    text = text.strip()
    return text if text else None


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
    
    # Step 1: HTML unescape (twice)
    text = html.unescape(html.unescape(text))
    
    # Step 2: Remove HTML tags
    text = BeautifulSoup(text, "html.parser").get_text("")
    
    # Step 3: Normalize whitespace and clean up
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+\.\s*", ". ", text)
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r"\s+\?", "?", text)
    text = re.sub(r"\s+!", "!", text)
    
    text = text.strip()
    return text if text else None


def classify_contribution(row: dict) -> tuple[str, str]:
    """
    Classify a contribution row as speech/procedural/noise.
    
    Returns: (row_type, classification_reason)
    
    Classification rules:
    - PROCEDURAL: Llywydd title OR contribution_type in {I, B}
    - NOISE: no Member_Id AND no text
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
    
    # Check if row is noise (no speaker, no text)
    if not member_id and not text:
        return ('noise', 'No speaker and no text')
    
    # Check if potential speech has substantive content
    if member_id and text:
        return ('speech', 'Valid speech')
    
    # Fallback: noise
    return ('noise', 'Insufficient content')
