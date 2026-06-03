# Senedd Database Queries

This directory contains sample SQL queries for analyzing and administering the Senedd parliamentary records database.

## Database Overview

**SQLite Location**: `senedd_records.db` (or via `DATABASE_URL` env var)

### Schema Summary (10 Tables)

#### Core Tables

**meetings**
- `meeting_id` (PK): Unique meeting identifier
- `assembly`: Assembly session number
- `meeting_date`: Date of meeting
- Stores metadata about parliamentary sessions

**members**
- `member_id` (PK): Unique member identifier
- `name_english`, `name_welsh`: Member names
- `job_title_english`, `job_title_welsh`: Current role (Llywydd, Minister, etc.)
- `biography_english`, `biography_welsh`: Member biographical info
- `sort_code`: Alphabetical sorting code

#### Contribution Tables

**raw_contributions**
- `contribution_id` (PK): Unique contribution identifier
- `meeting_id`, `agenda_item_id`: Foreign keys for context
- `member_id`: Who made the contribution (nullable)
- `contribution_verbatim`: Original text in language of delivery
- `contribution_type`: 'C'=contribution, 'B'=business, 'I'=information, 'O'=oral question
- `contribution_language`: 'Cy'=Welsh, 'En'=English
- `contribution_time`: Time of delivery (for video synchronization)
- `contribution_spoken_seneddtv`, `contribution_translated_seneddtv`: Video URLs

**clean_contributions**
- `contribution_id` (FK): Links to raw_contributions
- `cleaned_verbatim`: Text after HTML decode, tag removal, whitespace normalization

**classified_contributions**
- `contribution_id` (FK): Links to raw_contributions
- `row_type`: 'SPEECH' | 'PROCEDURAL' | 'NOISE'

#### Semantic Tables

**speeches**
- `speech_id` (PK): Unique speech identifier
- `meeting_id`, `member_id`, `agenda_item_id`: Context
- `speech_text`: Combined text from all parts
- `language_detected`: 'EN' or 'CY'
- **Key insight**: A "speech" = consecutive contributions from same speaker on same agenda item

**speech_parts**
- `speech_id`, `contribution_id`: Links speech to its constituent XML rows
- **Lineage**: Traces reconstructed speech back to 1-3 raw contributions
- Preserves order via contribution_order_id

**procedural_events**
- `event_type`: 'LLYWYDD' (chair statements) | 'MOTION' (procedural motions)
- `raw_text`: Procedural event content
- `contribution_type`: Source type from raw data

#### Admin Tables

**sync_checkpoints**
- `last_sync_date`: When this checkpoint was created
- `last_meeting_id`: Last meeting processed
- `file_count`: Number of files in this sync
- `status`: 'success' | 'partial' | 'error'
- Used for resumable incremental pipelines

**speech_embeddings** (Future)
- `speech_id`, `embedding_vector`: For vector search (not yet populated)

---

## Query Categories

### 1. **Basic Viewing** (`01_basic.sql`)
- View all speeches with speaker and agenda
- View member contributions by member
- List all meetings and their contribution counts

### 2. **Speech Reconstruction** (`02_reconstruction.sql`)
- View speeches with their constituent parts
- Find multi-part speeches (2+ parts per speaker+agenda)
- Trace contributions back to speeches

### 3. **Members & Contributors** (`03_members.sql`)
- Most active speakers by contribution count
- Member metadata and roles
- Members speaking by language (Welsh/English)

### 4. **Meetings & Agendas** (`04_meetings.sql`)
- Summary statistics per meeting
- Agenda items and their contribution counts
- Meeting timeline

### 5. **Quality & Validation** (`05_validation.sql`)
- Check for orphaned records (speeches with missing parts)
- Verify lineage (100% traceability)
- Empty contributions and classification distribution

### 6. **Admin & Sync** (`06_admin.sql`)
- View sync history and checkpoints
- Last sync date and coverage
- Processing status

### 7. **Advanced Analysis** (`07_analysis.sql`)
- Speech length distribution
- Language composition (Welsh vs English)
- Video URLs for synchronization

---

## How to Use

### Command Line

```bash
# Open SQLite CLI
sqlite3 senedd_records.db

# Load and run a query file
.read queries/01_basic.sql
```

### Python Integration

```python
import sqlite3

conn = sqlite3.connect('senedd_records.db')
cursor = conn.cursor()

# Read and execute query
with open('queries/01_basic.sql') as f:
    query = f.read()
    cursor.execute(query)
    results = cursor.fetchall()
    for row in results:
        print(row)
```

### Environment Variable

If using `DATABASE_URL` environment variable:

```bash
# Extract database file path from SQLite URL
# e.g., "sqlite:///./senedd_records.db" → "./senedd_records.db"
sqlite3 ./senedd_records.db < queries/01_basic.sql
```

---

## Key Concepts

### Speech vs Contribution
- **Contribution**: Single XML row (one speaker utterance)
- **Speech**: Logical group of consecutive contributions from same speaker on same agenda item
- **Ratio**: 207 contributions → 126 speeches (20% reduction via reconstruction)

### Lineage & Traceability
- Every speech has 1-3 speech_parts
- Every speech_part references a contribution_id
- 100% of speeches linked to raw contributions (verified)

### Classification
- **SPEECH**: Member content (type='C' + has speaker)
- **PROCEDURAL**: Procedural items (Llywydd statements, motions)
- **NOISE**: Metadata and formatting (no semantic content)

### Language Handling
- Welsh: `contribution_language = 'Cy'`
- English: `contribution_language = 'En'`
- Both stored; no filtering applied
- Speech may contain mixed languages

---

## Common Tasks

### Find speeches by a specific member
```sql
SELECT s.speech_id, s.speech_text, s.agenda_item_english
FROM speeches s
WHERE s.member_id = 5053  -- Huw Irranca-Davies
ORDER BY s.speech_id;
```

### Get meeting summary with stats
```sql
SELECT 
  m.meeting_date,
  COUNT(DISTINCT rc.contribution_id) as contributions,
  COUNT(DISTINCT s.speech_id) as speeches,
  COUNT(DISTINCT m.member_id) as members
FROM meetings m
LEFT JOIN raw_contributions rc ON m.meeting_id = rc.meeting_id
LEFT JOIN speeches s ON m.meeting_id = s.meeting_id
GROUP BY m.meeting_id;
```

### Find longest speeches
```sql
SELECT s.speech_id, m.name_english, LENGTH(s.speech_text) as length
FROM speeches s
LEFT JOIN members m ON s.member_id = m.member_id
ORDER BY length DESC
LIMIT 10;
```

### Check data quality
```sql
-- Verify all speeches have parts
SELECT COUNT(*) as orphaned
FROM speeches s
WHERE NOT EXISTS (
  SELECT 1 FROM speech_parts sp WHERE sp.speech_id = s.speech_id
);
```

---

## Performance Notes

- **Large meetings**: 200+ contributions per meeting
- **Typical query**: <100ms on 207 rows (SQLite)
- **Scaling**: Consider PostgreSQL for 10,000+ meetings
- **Indexes**: Currently on primary keys; add on `member_id`, `meeting_date` if needed

---

## Next Steps

1. Run `01_basic.sql` to familiarize with data structure
2. Explore `03_members.sql` to see active speakers
3. Run `05_validation.sql` to verify data integrity
4. Use `07_analysis.sql` for insights on speech patterns

For more complex queries, combine these patterns with your specific analysis goals.
