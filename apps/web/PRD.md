# Senedd Web — PRD / Architecture (Phase 2)

Planning doc for the Next.js frontend, written before building it. Scope:
MVP per the session brief — meeting search, video + synced transcript,
LLM/MCP query with citation blocks.

## 1. Layout

Four-quadrant workspace (single screen, deep-linkable):

```
┌────────────────────────┬──────────────────────────────┐
│ LEFT (≈38%)            │ TOP RIGHT                    │
│                        │ Video player (SeneddTV       │
│ • Search bar: meetings │ iframe) — or nothing if      │
│   by name/date +       │ embed unavailable            │
│   "Ask" (LLM) mode     ├──────────────────────────────┤
│ • Results list /       │ BOTTOM RIGHT                 │
│   LLM output area      │ Transcript pane, scrollable, │
│   (citation blocks)    │ synced to playback position  │
└────────────────────────┴──────────────────────────────┘
```

If video embedding is off/unavailable the right-hand side is entirely
transcript (the brief's fallback), with per-speech "watch on Senedd.tv ↗"
links preserving the jump-to-moment affordance externally.

## 2. Video / transcript sync — what the data actually supports

### Findings

- **Stored URLs.** Every `raw_contributions` row carries
  `contribution_spoken_seneddtv` / `contribution_translated_seneddtv` —
  URLs of the form `…senedd.tv/en/{clipId}?startPos={seconds}`.
  `speech_parts` carries these through per reconstructed speech
  (`spoken_url` of the first part = the speech's start).
- **Timing granularity.** `contribution_time` is a wall-clock datetime per
  contribution, and prior analysis (`analysis/wpm_fidelity.py`) verified it
  is **equivalent to the `startPos` seconds parameter** on the SeneddTV URL.
  So the corpus has **contribution-level (≈ sentence-to-paragraph, seconds
  precision) timestamps** — no per-word or per-sentence alignment exists.
- **Embeddability.** The Senedd states clips from Plenary can be embedded on
  third-party sites (senedd.wales "images and video" guidance; OGL v3.0
  data). However, this dev environment egress-blocks `senedd.tv`, so
  **X-Frame-Options / CSP `frame-ancestors` on the player page could not be
  verified here**. There is also **no documented postMessage/JS API** for the
  player — an iframe cannot report its playback position to us.

### Consequences → sync design (speech-boundary granularity, one-way)

1. **Jump-to-moment works and is exact**: clicking a speech (or a citation)
   sets the iframe `src` to the speech's own stored URL (its `startPos` is
   authoritative — no clock math). This is the interaction the MVP builds on.
2. **Continuous "transcript follows video" is not possible with an iframe**
   (no position events). Instead the transcript tracks a **virtual clock**:
   when the user jumps to a speech we know its `startPos`; a timer advances
   the highlight through subsequent speeches using their relative
   `startPos` deltas. Honest-but-approximate (pausing the video desyncs it),
   clearly labelled, and trivially removable if it annoys.
3. **Fallback ladder** (config `NEXT_PUBLIC_VIDEO_MODE=embed|link|off`):
   - `embed` (default): iframe with `onError`/load-failure notice offering
     the external link. If real-world testing shows senedd.tv denies
     framing, flip the env var — no code change.
   - `link`: no iframe; RHS is all transcript; speeches keep "watch ↗".
   - Future upgrade path: if a raw HLS stream URL is ever identified, a
     native `<video>` player provides `timeupdate` events and true two-way
     sync at the same speech-boundary granularity. Out of MVP scope.

## 3. Data access: direct Postgres, MCP only for the LLM path

**Decision: the frontend reads Postgres directly (read-only) for search +
transcripts, and talks to the MCP server only inside the `/api/ask` LLM
loop.**

- Meeting search and transcript fetch are simple, hot, deterministic queries;
  putting the MCP server (Python, an extra process, LLM-shaped result
  envelopes) in that path buys nothing and costs latency + an availability
  dependency. The DB schema is the stable contract between services.
- The MCP layer is where the *agentic* value is (semantic search, filters,
  member resolution, vote bridging) — exactly what an LLM needs. Routing the
  ask-box through Anthropic tool-use ↔ MCP exercises the same tools any MCP
  client would use, which is the point of the demo.
- Semantic search requires query embedding with the active model — that
  logic lives in Python (`senedd_search`); we do not reimplement it in TS.
- **Neon swap = config.** The web app uses the standard `pg` Pool with
  `DATABASE_URL` (+ TLS when the URL demands it). Point it at a Neon pooled
  connection string and it works; `@neondatabase/serverless` is a later
  drop-in inside the same `lib/db.ts` seam if Vercel edge/latency warrants.

## 4. Routes & data fetching

App Router; Server Components for first paint, route handlers for
client-driven interactions.

| Route | Kind | Purpose |
|---|---|---|
| `/` | RSC | Workspace with most recent meeting preselected |
| `/meetings/[id]` | RSC | Workspace focused on a meeting; `?speech=` deep-link scrolls/jumps |
| `GET /api/meetings?q=&from=&to=` | handler | Search-as-you-type: date/type/agenda-text match |
| `GET /api/meetings/[id]/transcript` | handler | Ordered speeches + `startPos` + agenda items |
| `POST /api/ask` | handler | LLM loop: Anthropic tool-use ↔ MCP server (HTTP); returns typed blocks |

RSC does the initial transcript render (fast first paint, no client
waterfall); the same SQL lives in `lib/queries.ts` shared by page and
handlers. Everything below the shell is client-side (video state, virtual
clock, ask box).

`/api/ask` contract returns **typed blocks**, not a string:
`{ blocks: [ {type:"prose", md} | {type:"citation", speechId, speaker, date,
meetingId, agendaItem, quote, startPos, tvUrl} | {type:"notice", text} ] }`.
When `ANTHROPIC_API_KEY` or the MCP URL is absent, the handler serves a
**mock mode** (deterministic answer built from real DB rows) so the UI and
citation grammar are fully exercisable in dev sandboxes — clearly labelled
in the UI.

## 5. LLM output formatting — candidates and choice

The output area must distinguish LLM prose from evidence. Candidates:

- **A. Footnote markers + citation cards.** Prose paragraphs carry inline
  superscript markers (¹ ²); each maps to a bordered citation card beneath
  the answer (speaker avatar/initials, name, date, meeting, quoted excerpt,
  ▶ play). Hovering a marker previews the card; clicking either jumps
  video+transcript.
- **B. Interleaved evidence blocks.** Prose and full-width quote blocks
  alternate in document order, research-memo style. Strong provenance but
  chops the prose rhythm and duplicates content when one speech supports
  several sentences.
- **C. Inline speaker chips.** Speaker-name pills embedded in the prose act
  as citations. Compact, but ambiguous when a speaker has several speeches
  and unreadable when a sentence cites three sources.

**Choice: A** — it keeps prose scannable while giving citations a stable,
reusable visual grammar (the same card renders MCP search hits elsewhere),
and hover-then-click matches the brief exactly. Marker↔card is 1:1 with
tool-result speeches, so no invented provenance.

Block grammar (v1): `prose` (rendered markdown, plain background),
`citation` (card: accent left border, quote + meta + play affordance),
`notice` (muted italic status: mock mode, tool errors, "no results").

## 6. MVP cut & non-goals

In: meeting search (name/date), transcript pane from `speeches` +
`speech_parts` timing, iframe player with jump-to-`startPos`, virtual-clock
highlight, `/api/ask` with citation cards (real loop when keys present, mock
otherwise), Vercel-deployable (`apps/web` as project root).

Out (deliberately): auth, Welsh-language UI toggle (data is bilingual; UI
copy is English-first for MVP), streaming LLM responses, votes/QNR surfaces,
HLS/native player, transcript full-text search (semantic search belongs to
the ask box).

## 7. Risks

- **senedd.tv may deny framing** → `NEXT_PUBLIC_VIDEO_MODE=link` fallback is
  first-class, not an afterthought (this sandbox couldn't verify headers).
- **Virtual clock drift** (pauses/buffering) → visibly labelled "approximate
  follow"; jump actions always resync exactly.
- **This sandbox has no real corpus** (Senedd hosts egress-blocked) →
  `scripts/seed_fixture.py` loads a clearly-marked synthetic meeting through
  the *real* transformation pipeline; on a normal network the backfill
  populates real data with zero frontend changes.
