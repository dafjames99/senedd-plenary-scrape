# 📌 Senedd Plenary XML → Speech Reconstruction + Embedding Pipeline (Plan of Action)

## 1. Goal

Transform raw Senedd plenary XML transcript data into a **clean, semantically meaningful speech-level dataset** suitable for:

* vector embeddings (semantic search)
* speaker-level analytics
* traceable retrieval back to original transcript/video

Key idea:

> The XML rows are *not* the semantic unit. We reconstruct them into **contiguous speeches**.

---

# 2. Core Problem Observed

The raw XML export has structural artifacts:

### 2.1 Fragmented speeches

A single spoken intervention may be split across multiple XML rows:

* consecutive `Contribution_ID`s
* same speaker
* same agenda item
* small time gaps (seconds)
* sentence continuation across rows

➡️ Therefore: **1 XML row ≠ 1 speech**

---

### 2.2 Empty placeholder rows

Repeated pattern:

```xml
<contribution_type>C</contribution_type>
<Member_Id></Member_Id>
<contribution_verbatim />
```

These are:

* alignment artifacts
* bilingual structure placeholders
* not meaningful speech content

➡️ Must be removed or ignored in grouping logic

---

### 2.3 HTML + encoding noise in text

Common issues:

* double-escaped entities: `&amp;amp;`
* HTML markup: `<p>`, `<em>`, `<span>`
* non-breaking spaces: `&nbsp;`
* duplicated phrases due to segmentation

➡️ Must be cleaned before embedding

---

### 2.4 Agenda transitions inside same speaker

Presiding Officer rows show:

* same speaker
* different agenda item

These represent **procedural transitions**, not speech continuation

➡️ Agenda ID is a valid segmentation boundary

---

### 2.5 Contribution type semantics (observed, not fully verified)

Empirical hypothesis:

| Type | Likely meaning                          |
| ---- | --------------------------------------- |
| C    | spoken contribution                     |
| O    | oral question                           |
| B    | procedural block / heading              |
| I    | instruction / motion / formal statement |

➡️ Useful for filtering but NOT primary grouping key

---

# 3. Clean Data Strategy (High-Level)

## Step 1 — Parse XML into dataframe

Use `pd.read_xml()`

---

## Step 2 — Clean text fields

For `contribution_verbatim`:

* HTML decode twice (`html.unescape`)
* strip HTML tags (BeautifulSoup recommended)
* normalize whitespace
* remove NBSP

---

## Step 3 — ROUTE ROWS

```
IF Llywydd OR contribution_type in {I, B} → procedural_events
ELSE IF Member_Id missing AND no text → discard
ELSE → speech_candidates
```

This filters out procedural rows from speech rows. 

**NOTE**: A row is *not* a "speech" if `Member_job_title_English` == "The LLywydd". These are non-substantive because they are presiding officer contributions. The same goes for procedural entries (where contribution_type is "I" or "B").


## Step 4 — Define “substantive row”

Of the speech_candidate rows, A row is valid if:

* has speaker (`Member_Id not null`)
* has text (non-empty after cleaning)

---

## Step 5 — Speech reconstruction rule

A **speech begins when:**

```text
speaker changes OR agenda changes
```

Formally:

```python
new_speech =
    (Member_Id != previous Member_Id)
    OR
    (Agenda_Item_ID != previous Agenda_Item_ID)
```

Empty rows are ignored entirely.

---

## Step 6 — Speech construction logic

Iterate over cleaned rows:

* maintain `current_speech_id`
* append text when same speech
* start new speech when rule triggers

---

## Step 7 — Merge fragments within speech

Within same speech:

* concatenate `contribution_verbatim`
* preserve ordering via `Contribution_Order_ID`

Optional:

* deduplicate overlapping sentence fragments

---

## Step 8 — Preserve metadata properly

### Speech-level metadata (final table)

Each speech should retain:

* `speech_id`
* `speaker_id`
* `speaker_name`
* `agenda_item_id`
* `meeting_id`
* `date/time (optional)`
* `combined_speech_text`

---

### Speech-part lineage table (important)

Preserve mapping:

| speech_id | contribution_id | order_id |
| --------- | --------------- | -------- |

Optional but recommended:

* `senedd_tv_url` list per speech

# 5. Embedding Strategy

## Recommended approach:

* embed **speech-level text (NOT XML rows)**
* use **English translation OR bilingual concatenation**
* keep speeches as atomic semantic units

---

# 6. Key Design Decisions (Final Summary)

### DO:

✔ Collapse XML rows → speeches
✔ Ignore empty placeholder rows
✔ Use agenda + speaker as segmentation boundary
✔ Preserve mapping back to original contributions
✔ Embed reconstructed speeches only

---

### DO NOT:

✘ Embed raw XML rows
✘ Treat contribution rows as semantic units
✘ Over-clean text (avoid semantic distortion)
✘ Rely heavily on `contribution_type` for grouping

---

# 7. Mental Model to Carry Forward

> XML rows are “audio segmentation artifacts”
> Speeches are the true semantic objects
> Embeddings index speeches, not rows

---

# 8. Output of Pipeline

Final output is a structured corpus:

* clean speeches
* traceable back to XML
* suitable for semantic search over parliamentary discourse
* optionally linked to video timestamps

---

# NEXT STEP (EVENTUAL NEXT TASK)
The natural continuation is:

> “turn this into a streaming pipeline (so it works on large XML files efficiently, not just pandas-in-memory)”



TO REITERATE:
# 📊 FINAL DATA ARCHITECTURE (AGENT-READY SPEC)

## 🔴 KEY PRINCIPLE

You are transforming:

> 1 XML row ≠ 1 semantic unit

into:

> 1 SPEECH ≈ 1 coherent discourse act (embedding unit)

Everything else becomes either:

* lineage (traceability)
* metadata (normalisation tables)
* procedural structure (non-speech events)

---

# 1. RAW INPUT TABLE (UNCHANGED SOURCE)

## `raw_contributions`

This is your **direct XML ingestion output**.

### Columns (exactly your current schema):

```text
Meeting_ID                  (int / string)
Assembly                    (int)
MeetingDate                (datetime)
Contribution_ID           (int, PK from XML)
Contribution_Order_ID     (int)
contribution_language     (str: "En" | "Cy")
ContributionTime          (datetime)

contribution_spoken_seneddTv     (string URL)
contribution_translated_seneddTv  (string URL)

Agenda_Item_ID            (string)
Agenda_item_welsh         (string)
Agenda_item_english       (string)

contribution_type         (categorical: C | O | B | I)

Attendee_Id               (int | nullable)
Member_Id                 (int | nullable)

Member_name_English       (string | nullable)
Member_biog_English       (string | nullable)
Member_biog_Welsh         (string | nullable)
Member_job_title_English  (string | nullable)
Member_job_title_Welsh    (string | nullable)
Member_Sortcode           (string | nullable)

Contribution_English      (string | nullable)
Contribution_Welsh        (string | nullable)

contribution_verbatim     (string | HTML encoded | nullable)
contribution_translated   (string | HTML encoded | nullable)
```

### Notes:

* This table is NEVER modified structurally
* All downstream tables reference it

---

# 2. CLEANED ROW TABLE (TEXT NORMALISED)

## `clean_contributions`

Same schema as raw, but:

### Transformations applied:

* HTML stripped from `contribution_verbatim` / `translated`
* `&amp;nbsp;`, `&amp;amp;` decoded
* whitespace normalised
* empty strings → NULL

---

# 3. ROW CLASSIFICATION OUTPUT (CRITICAL ROUTING LAYER)

## `classified_contributions`

Adds one key field:

```text
row_type (ENUM):
    - "speech"
    - "procedural"
    - "noise"
```

### Classification rules:

#### speech:

* Member_Id NOT NULL
* NOT Llywydd
* contribution_type == "C" (primary filter, but not sole rule)
* has substantive text

#### procedural:

* Member_job_title contains "Llywydd"
  OR
* contribution_type IN {"I", "B"}
  OR
* known procedural phrase patterns

#### noise:

* empty contribution_verbatim
* missing speaker AND no semantic text

---

# 4. SPEECH TABLE (PRIMARY EMBEDDING UNIT)

## `speeches`

This is your **core semantic dataset**

```text
speech_id              (generated UUID or incremental int)

meeting_id             (FK)
assembly               (int)
agenda_item_id         (string)
speaker_id             (FK → members.member_id)

speaker_name           (string, denormalised for convenience)

speech_language        (En / Cy / Mixed)

speech_text            (FULL concatenated cleaned text)

start_time             (ContributionTime min)
end_time               (ContributionTime max)

source_row_count       (int)
```

### Notes:

* THIS is what gets embedded
* speech_text is concatenation of multiple contributions
* ordering preserved by Contribution_Order_ID

---

# 5. SPEECH PARTS TABLE (TRACEABILITY LAYER)

## `speech_parts`

Maps speech → original XML structure

```text
speech_id              (FK → speeches)

Contribution_ID        (PK from raw XML)
Contribution_Order_ID

ContributionTime

spoken_url             (senedd TV spoken URL)
translated_url         (senedd TV translated URL)

verbatim_text          (cleaned snippet used in speech)
```

### Purpose:

* full audit trail
* debugging
* alignment to video

---

# 6. MEMBER DIMENSION TABLE (IMPORTANT NORMALISATION)

## `members`

You SHOULD explicitly construct this.

```text
member_id             (PK)

member_name_english
member_name_welsh

job_title_english
job_title_welsh

biography_english
biography_welsh

sort_code

is_current_member    (optional, if derivable)
```

### Why this matters:

Avoid repeating metadata across every speech row.

Instead:

* speeches → reference member_id
* members → single source of truth

---

# 7. PROCEDURAL EVENTS TABLE (STRUCTURAL LAYER)

## `procedural_events`

This is where:

* Llywydd interventions
* motions
* agenda transitions
* standing order references

go.

```text
procedural_id          (PK)

meeting_id
agenda_item_id

event_time

event_type:
    - "agenda_transition"
    - "motion_result"
    - "ruling"
    - "order_statement"
    - "instruction"

speaker_name           (nullable, often Llywydd)

raw_text

source_contribution_id (nullable)

senedd_tv_url
```

---

# 8. EMBEDDINGS TABLE (VECTOR LAYER)

## `speech_embeddings`

```text
speech_id         (FK → speeches)
embedding_vector  (float[] or binary)
model_name
created_at
```

---

# 9. HOW YOUR ORIGINAL COLUMNS ARE USED

## Direct mapping summary:

### Used in speeches:

* Meeting_ID
* Assembly
* MeetingDate
* Agenda_Item_ID
* contribution_language (aggregated)
* ContributionTime (min/max)
* Member_Id → speaker_id
* Member_name_English → denormalised speaker_name
* Contribution_Order_ID → ordering support

---

### Used only in speech_parts:

* Contribution_ID
* Contribution_Order_ID
* contribution_verbatim
* contribution_translated
* seneddTv URLs

---

### Used in members table:

* all Member_* fields

---

### Used in procedural_events:

* contribution_type (I, B heavily)
* Llywydd title
* agenda markers
* verbatim procedural text

---

# 10. CRITICAL PIPELINE ORDER (FINAL)

## STEP 0 — ingest raw XML

→ raw_contributions

---

## STEP 1 — clean text

→ clean_contributions

---

## STEP 2 — classify rows

→ classified_contributions

THIS is your **key routing step**

---

## STEP 3 — split branches

### speech branch:

→ speech reconstruction

### procedural branch:

→ procedural_events

### noise branch:

→ discard

---

## STEP 4 — build speeches

→ speeches table

---

## STEP 5 — build speech_parts

→ lineage mapping

---

## STEP 6 — build members table

→ normalization pass

---

## STEP 7 — embeddings

→ speech_embeddings