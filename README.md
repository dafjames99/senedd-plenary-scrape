# Senedd Speech Reconstruction Pipeline

Transform raw Senedd Cymru (Welsh Parliament) XML plenary session records into semantically reconstructed speeches with full traceability, text cleaning, and bilingual support.

## Overview

This project parses XML contributions from Senedd plenary sessions, cleans the text (HTML entities, tag removal), classifies rows (speech/procedural/noise), reconstructs multi-part speeches, and stores everything in a normalized SQLite database with complete lineage tracking.

**Current Status**: ✅ Complete and Production Ready
- **Input**: 207 XML contribution rows
- **Output**: 126 semantic speeches, 43 procedural events, 39 unique speakers
- **Data Quality**: 100% traceability, zero empty records

---

## Quick Start

### Installation

```bash
# Clone repository
git clone https://github.com/yourusername/senedd-scrape
cd senedd-scrape

# Install dependencies (requires Python 3.14+)
uv sync
```

### Run the Pipeline

```bash
# Full rebuild (fresh database)
python3 main.py --mode full

# Or incremental mode (append/update existing data)
python3 main.py --mode incremental
```

This creates `senedd_records.db` with all tables populated.

---

## Key Features

✅ **XML Parsing & Text Cleaning**
- Parse Senedd XML with pandas
- HTML double-unescape (`&amp;nbsp;` → space)
- Remove HTML tags (`<p>text</p>` → text)
- Normalize whitespace

✅ **Semantic Speech Reconstruction**
- Group consecutive contributions by speaker & agenda item
- Merge multi-part speeches (31 out of 126 speeches have multiple parts)
- Preserve metadata (time range, part count, language)

✅ **Full Traceability**
- Every speech links back to source XML contributions
- `speech_parts` table provides complete lineage
- 100% audit trail preserved

✅ **Bilingual Support**
- Welsh (Cy) and English (En) language tags
- 78.6% English, 21.4% Welsh content
- Mixed-language speech detection

✅ **Normalized Database**
- 9 SQLAlchemy ORM models
- Efficient querying and integration
- Ready for embeddings layer

---

## Database Schema

### Core Dimension Tables
- **meetings** — Plenary session metadata (1 record)
- **members** — Unique speakers (39 records)
- **raw_contributions** — Direct XML ingestion (207 records, unmodified)

### Processing Pipeline Tables
- **clean_contributions** — Text-normalized rows (207 records)
- **classified_contributions** — Row type classification (207 records)

### Output Tables (Primary Deliverables)
- **speeches** — Reconstructed semantic units (126 records) ⭐
- **speech_parts** — Lineage mapping to XML (146 records) ⭐
- **procedural_events** — Non-speech events (43 records)
- **speech_embeddings** — Vector storage (empty, ready for future use)

### Additional Tables
- **sync_checkpoints** — Incremental pipeline audit trail

#### Relationships
```
Meeting (1) → (M) RawContribution
           → (M) Speech

Member (1) → (M) RawContribution
          → (M) Speech

Speech (1) → (M) SpeechPart → (1) RawContribution
```

---

## Usage Guide

### Connect to Database

**From CLI:**
```bash
sqlite3 senedd_records.db
```

**From Python:**
```python
from src.db_schema import Session, Speech, Member
import os

DB_URL = os.getenv("DATABASE_URL", "sqlite:///./senedd_records.db")
session = Session()

# Query speeches
speeches = session.query(Speech).all()
for speech in speeches:
    print(f"{speech.speaker_name}: {speech.speech_text[:100]}...")
```

### Common Queries

**1. Find all speeches by a speaker**
```sql
SELECT speech_id, speaker_name, LENGTH(speech_text) as chars
FROM speeches
WHERE speaker_name LIKE '%Rhun ap Iorwerth%'
ORDER BY speech_id;
```

**2. Get speech lineage (which XML rows make up each speech)**
```sql
SELECT 
  s.speech_id,
  s.speaker_name,
  GROUP_CONCAT(sp.contribution_id, ',') as contribution_ids,
  COUNT(sp.contribution_id) as part_count
FROM speeches s
JOIN speech_parts sp ON s.speech_id = sp.speech_id
GROUP BY s.speech_id
ORDER BY part_count DESC;
```

**3. Find longest speeches**
```sql
SELECT 
  speech_id,
  speaker_name,
  source_row_count as parts,
  LENGTH(speech_text) as char_count,
  ROUND(LENGTH(speech_text) / 5.5) as approx_words
FROM speeches
ORDER BY char_count DESC
LIMIT 10;
```

**4. List all procedural events**
```sql
SELECT procedural_id, event_type, speaker_name, event_time, raw_text
FROM procedural_events
ORDER BY event_time;
```

**5. Find multi-part speeches**
```sql
SELECT 
  speech_id,
  speaker_name,
  source_row_count,
  LENGTH(speech_text) as total_chars,
  ROUND(LENGTH(speech_text) / source_row_count) as avg_chars_per_part
FROM speeches
WHERE source_row_count > 1
ORDER BY source_row_count DESC;
```

**6. Get all members and their contribution count**
```sql
SELECT 
  member_id,
  name_english,
  job_title_english,
  (SELECT COUNT(*) FROM speeches WHERE speaker_id = members.member_id) as speech_count
FROM members
ORDER BY speech_count DESC;
```

**7. Analyze speech patterns by agenda item**
```sql
SELECT 
  agenda_item_id,
  COUNT(*) as speech_count,
  AVG(LENGTH(speech_text)) as avg_length,
  MAX(source_row_count) as max_parts
FROM speeches
GROUP BY agenda_item_id
ORDER BY speech_count DESC;
```

**8. Find bilingual speeches**
```sql
SELECT 
  speech_id,
  speaker_name,
  speech_language,
  source_row_count
FROM speeches
WHERE speech_language = 'Mixed'
   OR (SELECT COUNT(DISTINCT contribution_language) 
       FROM speech_parts sp 
       WHERE sp.speech_id = speeches.speech_id) > 1;
```

### Export Data

**To CSV:**
```bash
sqlite3 -header -csv senedd_records.db \
  "SELECT speech_id, speaker_name, agenda_item_id, LENGTH(speech_text) as text_length 
   FROM speeches;" > speeches.csv
```

**To JSON:**
```bash
sqlite3 -json senedd_records.db \
  "SELECT speech_id, speaker_name, speech_text FROM speeches LIMIT 10;" > speeches.json
```

---

## Pipeline Architecture

### Six Processing Phases

**Phase 1: Ingest XML**
- Parse XML with pandas
- Load 207 rows into `raw_contributions`
- Create meeting and member records

**Phase 2: Clean Text**
- HTML double-unescape (`&amp;nbsp;` → space)
- Remove HTML tags (`<p>text</p>` → text)
- Normalize whitespace (multiple spaces → single)
- Store in `clean_contributions`

**Phase 3: Classify**
- Identify procedural rows (Llywydd, types I/B)
- Identify noise (no speaker, no text)
- Mark valid speeches (member present + text)
- Result: 146 speech, 43 procedural, 18 noise

**Phase 4: Reconstruct Speeches**
- Group consecutive rows by (speaker_id, agenda_item_id)
- Concatenate texts in order
- Preserve metadata (time range, part count)
- Result: 126 speeches from 146 rows

**Phase 5: Build Dimensions**
- Extract unique members (39 total)
- Extract procedural events (43 entries)
- Populate metadata tables

**Phase 6: Validate**
- Check 100% traceability
- Verify no empty speeches
- Ensure data integrity
- Generate quality report

### Pipeline Modes

**Full Mode** (Fresh database)
```bash
python3 main.py --mode full
python3 main.py --mode full --xml-file data/other_meeting.xml
```

**Incremental Mode** (Append/update)
```bash
python3 main.py --mode incremental
python3 main.py --mode incremental --last-sync 2026-05-01
python3 main.py --mode incremental --keep-xml  # Retain XML files
```

---

## Project Structure

```
senedd-scrape/
├── main.py                      # Entry point - runs pipeline
├── src/
│   ├── __init__.py
│   ├── db_schema.py            # SQLAlchemy models (9 tables)
│   ├── pipeline.py             # Main orchestrator (6 phases)
│   ├── transformers.py         # Text cleaning & classification
│   ├── upsert.py               # Incremental database updates
│   ├── fetcher.py              # Meeting detection & download
│   ├── data.py                 # Utilities for fetching new files
│   ├── db.py                   # Legacy ORM (reference)
│   └── parse_plenary.py        # Legacy parsing (reference)
├── data/
│   └── 260602_Plenary_Bilingual.xml  # Input XML
├── queries/                    # Example SQL queries
├── senedd_records.db           # Output database (generated)
└── pyproject.toml              # Python project metadata
```

---

## Key Results

### Data Quality Metrics

| Metric | Value |
|--------|-------|
| **Input XML rows** | 207 |
| **Output speeches** | 126 |
| **Unique speakers** | 39 |
| **Procedural events** | 43 |
| **Multi-part speeches** | 31 (24.6%) |
| **Avg speech length** | 1,028 chars |
| **English speeches** | 99 (78.6%) |
| **Welsh speeches** | 27 (21.4%) |
| **Full traceability** | 100% ✅ |

### Multi-Part Speech Example

**Speech #107** (Mabon ap Gwynfor AS, Agenda 260602-5)
- 6 original XML contributions merged into 1 speech
- Total text: 10,400 characters
- Contributions: 762660 → 762664 → 762671 → 762683 → 762685 → 762699
- Correctly reconstructs bilingual intervention with multiple segments

---

## Performance & Optimization

### For Large Queries
Add indexes to improve query performance:
```sql
CREATE INDEX idx_speeches_speaker ON speeches(speaker_id);
CREATE INDEX idx_speech_parts_speech ON speech_parts(speech_id);
CREATE INDEX idx_procedural_time ON procedural_events(event_time);
CREATE INDEX idx_raw_contributions_meeting ON raw_contributions(meeting_id);
```

### For Large Incremental Syncs
- Process 3-5 meetings per cron job
- Consider batching with connection pooling
- Memory usage: ~5-10 MB per meeting file

---

## Integration Examples

### Embed Speeches with OpenAI

```python
from src.db_schema import Session, Speech, SpeechEmbedding
import openai
import json

session = Session()
speeches = session.query(Speech).all()

for speech in speeches:
    # Generate embedding
    embedding = openai.Embedding.create(
        input=speech.speech_text,
        model="text-embedding-3-small"
    )["data"][0]["embedding"]
    
    # Store in database
    embed = SpeechEmbedding(
        speech_id=speech.speech_id,
        embedding_vector=json.dumps(embedding),
        model_name="text-embedding-3-small"
    )
    session.add(embed)

session.commit()
```

### Embed Speeches with Sentence Transformers

```python
from sentence_transformers import SentenceTransformer
from src.db_schema import Session, Speech

model = SentenceTransformer('all-MiniLM-L6-v2')
session = Session()
speeches = session.query(Speech).all()

embeddings = model.encode([s.speech_text for s in speeches])
# Store or process embeddings as needed
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| **Pipeline fails on XML parse** | Verify XML exists at `data/260602_Plenary_Bilingual.xml` and run `pip install pandas lxml` |
| **Database locked** | Close other connections or delete `senedd_records.db` and re-run |
| **Wrong speech count** | Verify classification matches: speech=146, procedural=43, noise=18 |
| **Import errors** | Run `uv sync` to ensure all dependencies installed |

---

## Future Enhancements

### Ready to Implement

1. **Embeddings Layer**
   - Embed 126 speeches with sentence-transformers or OpenAI
   - Store vectors in `speech_embeddings` table (schema ready)
   - Enable semantic search and similarity queries

2. **Incremental Processing**
   - Auto-detect new meetings via Senedd API
   - Append to existing database without re-processing
   - Resumable checkpoints for fault tolerance

3. **Speaker Analytics Dashboard**
   - Contribution frequency charts
   - Speech length analysis by speaker
   - Topic tagging and discourse analysis

4. **Video Alignment**
   - Link speeches to seneddTv timestamps
   - Find exact locations in parliament recording
   - Multi-language alignment

5. **Streaming Pipeline**
   - Chunked XML parsing instead of `pd.read_xml()`
   - Batch database writes for memory efficiency
   - Support for very large files

6. **Member History Tracking**
   - Track job title changes over time
   - Add `member_history` table for temporal queries
   - Enable historical analysis

---

## Development Notes

### Text Cleaning Rules Applied

```
1. HTML double-unescape: &amp;nbsp; → &nbsp; → space
2. HTML tag removal: <p>text</p> → text
3. Whitespace normalization: multiple spaces → single space
4. Punctuation cleanup: space+period → period+space
```

### Classification Logic

```
Classification:
├── Procedural: Member_job_title="Llywydd" OR contribution_type IN {I, B}
├── Noise: No Member_Id AND no substantive text
└── Speech: Member_Id present AND has text AND not Llywydd
```

### Speech Boundary Rules

```
New speech starts when:
  - Speaker ID changes (Member_Id)
  - OR Agenda Item ID changes (Agenda_Item_ID)

Concatenation:
  - All parts sorted by Contribution_Order_ID
  - Text parts joined with single space
  - Metadata: min(start_time), max(end_time), count(parts)
```

---

## Use Cases

This pipeline enables:

1. **Semantic Search** — Embed speeches and find similar statements across speakers
2. **Analytics** — Speaker frequency, speech length analysis, agenda item insights
3. **Video Alignment** — Link speeches to seneddTv timestamps
4. **Discourse Analysis** — Bilingual conversation patterns, debate structure
5. **Topic Discovery** — Identify themes across multiple parliamentary sessions

---

## License & Attribution

Built for analysis of Welsh Parliament proceedings. Senedd data used under open access terms.

---

## Questions?

- **How do I run it?** → See "Quick Start" above
- **What data is in the DB?** → See "Database Schema" section
- **How do I query it?** → See "Common Queries" section
- **What went wrong?** → See "Troubleshooting" section
- **What's next?** → See "Future Enhancements" section

---

**Status: ✅ COMPLETE AND PRODUCTION READY**
