from pathlib import Path
from bs4 import BeautifulSoup
import pandas as pd
import html
import re
from datetime import datetime 

date_string = "260602" # --- TEMPORARY --- REPLACE with:
# ---
# date_string = datetime.strftime(date, "%Y%m%d")[2:]
# ----

filename = f"{date_string}_Plenary_Bilingual"
PROJECT_ROOT = Path(__file__).parents[1]
DATA_DIR = PROJECT_ROOT / "data"
FILE = DATA_DIR / f"{filename}.xml"

def clean_transcript(text: str) -> str:
    if not isinstance(text, str):
        return text
    text = html.unescape(html.unescape(text))
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+\.\s*", ". ", text)
    text = re.sub(r"\s+,", ",", text)
    return text.strip()

def remove_html_tags(text: str) -> str:
    if not isinstance(text, str):
        return text
    return BeautifulSoup(text, "html.parser").get_text("")

df = pd.read_xml(FILE)

text_cols = df.select_dtypes(include=["object", "str"]).columns
contribution_text_cols = ["contribution_verbatim", "contribution_translated"]
for col in text_cols:
    df[col] = df[col].map(clean_transcript)
for col in contribution_text_cols:
    df[col] = df[col].map(remove_html_tags)
    
print(df.columns)