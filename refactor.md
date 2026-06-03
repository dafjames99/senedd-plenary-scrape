## Architectural Critique: The Core Issues

### 1. Functional Duplication & Divergent Logic

You have an identity crisis between global runs and incremental runs. For instance:

* `clean_text_fields()` extracts fields like `contribution_translated_clean`.
* `_clean_meeting_contributions()` extracts fields into `cleaned_verbatim`—which **changes the schema column names** depending on whether you run a full or incremental rebuild! This is a massive data bug waiting to happen.
* Row classification signatures diverge entirely: one takes a constructed dictionary (`classify_contribution(row_dict)`), while the other takes raw keyword parameters.

### 2. Side-Effect Heavy, Non-Idempotent Code

The pipeline stages directly manipulate database persistence inside their core loop logic. If `reconstruct_speeches` fails halfway through, your database is left in a corrupted state. State tracking (e.g., matching the previous speech boundary) relies entirely on the sorting order of the query buffer in memory, rather than deterministic upsert keys.

### 3. Conflating Fetching/Orchestration with Data Transformations

`SeneddPipeline` is doing too much. It handles SQL connection pooling, XML file-system management, explicit Pandas type conversion parsing, and the business logic of speech grouping. This makes unit testing impossible without setting up an actual database and mock file trees.

---

## The Refactor Blueprint

To align the architecture with a true **"Continual Updates"** mentality, we need to shift from an imperative orchestration model to a **Pure Functional / Event-Driven model bounded by Database Transactions**.

```
[ Raw XML / Data Stream ] 
          │
          ▼
   ┌─────────────┐
   │ Ingest Engine│ ──> Writes to Raw SQL Layers (Idempotent Upsert)
   └─────────────┘
          │ (Yields a target chunk, e.g., meeting_id)
          ▼
   ┌─────────────┐
   │ Transformer │ ──> Runs pure stateless transformations 
   └─────────────┘     (Clean, Classify, Boundary Detection)
          │
          ▼
   ┌─────────────┐
   │ DB Unit-of- │ ──> Atomically saves or updates downstream blocks 
   │    Work     │     within a single explicit transaction.
   └─────────────┘

```

---

## Refactor Plan Context for Coding Agent

Save the following specification markdown block as a prompt/file context for your coding agent.

```markdown
# Target Specification: Shift Pipeline to Idempotent, Unified Chunks

## Objective
Refactor the Senedd speech processing pipeline (`pipeline.py` and `db_schema.py`) to treat continuous incremental additions as the primary, default operational pathway. Eliminate dual code paths between "full" and "incremental" runs by processing data in uniform atomic chunks (grouped by `meeting_id`), utilizing idempotent database operations.

## Branch Name
`refactor/continual-update-architecture`

## Architectural Requirements

### 1. Unified Interface Strategy
* Change all transformation functions within `SeneddPipeline` to accept an explicit transaction session and a slice parameter: `meeting_id: Optional[int] = None`.
* If `meeting_id` is passed, filter queries to that explicit meeting. If `None`, perform the calculation globally across all un-processed records.
* Rewrite `run_full_pipeline` so it merely acts as a macro loop that calls the incremental loop across all identified target IDs.

### 2. Fix Schema Drift & Structural Defects
* Standardize the target columns between the full and incremental versions. Ensure `CleanContribution` consistently writes to identical variable names across paths.
* Fix the incomplete model cutoff at the tail end of `db_schema.py` for `RawContribution` and complete its parameters cleanly.

### 3. Extract Stateless Transformers
* Move parsing and pandas type conversions out of `pipeline.py`.
* Build a clear boundary: The pipeline core should read raw rows, stream them through data-cleansing modules, execute the state machine grouping for boundary detection, and pass the results to a write layer.

### 4. Implement Atomic Transactions & Idempotency
* Wrap all operations for a single `meeting_id` into a unified transactional block (`with session.begin():`). 
* Replace direct `.add()` tracking patterns with native SQLAlchemy `merge()` or `INSERT ... ON CONFLICT DO UPDATE` up-serts based on natural unique keys (`contribution_id`, `meeting_id`, `member_id`). This eliminates the explicit and messy pre-delete statements (`upserter.delete_meeting_speeches`).

## Step-by-Step Execution Plan

### Step 1: Complete and Verify Schema Definitions
Fix `db_schema.py`. Ensure that all relationships feature cascade deletion properties where appropriate, and that all tracking metrics maintain programmatic integrity across continuous ingest matches.

### Step 2: Unify Core Methods
Refactor methods to match this unified signature convention:
```python
def clean_text_fields(self, session, meeting_id: Optional[int] = None) -> int:
    query = session.query(RawContribution)
    if meeting_id:
        query = query.filter(RawContribution.meeting_id == meeting_id)
    # Ensure processing loop writes to schema definitions cleanly...

```

### Step 3: Rewrite Speech Reconstructions

Standardize speech and procedural element boundary group assignments. Ensure the aggregation checks speaker and agenda changes deterministically, handling trailing buffers correctly for both single meetings and historical processing runs.

### Step 4: Streamline Orchestration Mechanics

Refactor the high-level operational loops down to clean sequences:

```python
def process_meetings(self, meeting_ids: list[int]):
    for m_id in meeting_ids:
        with self.SessionLocal() as session:
            with session.begin():
                # Sequential operations execute atomically per meeting chunk
                self._clean_meeting_contributions(session, m_id)
                self._classify_meeting_rows(session, m_id)
                self._reconstruct_meeting_speeches(session, m_id)

```

### Step 5: Validate and Verify Performance

Verify code coverage. Provide validation benchmarks demonstrating that parsing an existing ID multiple times does not corrupt relational records or duplicate downstream data lines.

```

---

### What to check when the agent is done
Once your agent has processed this plan, look closely at its implementation of `reconstruct_speeches`. Ensure that it cleanly handles boundaries between consecutive meetings if a full run is executed, and double-check that it uses robust transaction management (like standard context managers) rather than manually handling `.commit()` and `.close()` sequences inside individual loops.