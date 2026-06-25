# Senedd MCP — Manual Testing Checklist

A human-readable smoke-test script for the Senedd MCP server, run from a real
client (e.g. Claude Desktop). Each entry gives an explicit **INPUT** to type at
the client and the **EXPECT** behaviour to verify.

> **Note on data coverage.** The local corpus is large for *speeches* but tiny
> for the new sources — Votes and written QNR publish late and sparsely. At time
> of writing the local DB holds:
>
> - **Votes:** 5 votes, all in meeting `15798`, agenda item `260325-10`
>   (*Welsh Conservatives Debate — The Welsh Government*: 1 motion + 4
>   amendments, **all rejected**; 305 per-member vote records).
> - **Written QNR:** 4 contributions in meeting `16060`, agenda `260602-QNR`
>   (Tom Montgomery Q → First Minister A on local-government finances;
>   Steve Bayliss Q → First Minister A on cashless bus payments).
>
> So judge **plumbing correctness** here (right tool, right shape, right
> citations), not retrieval *quality* — quality needs more data (Phase 5).

Run `senedd://corpus-stats` first and update the numbers above if they have
changed.

---

## 1. Source-aware semantic search

### 1.1 Span all sources
**INPUT:**
```
Search the Senedd record for "bus services" across all sources — show spoken
speeches, written answers, and votes, and label each result with its source.
```
**EXPECT:**
- Results interleaved by rank, each carrying a `source_type` of `speech`,
  `written`, or `vote`.
- The Steve Bayliss / First Minister bus exchange appears among `written` hits.
- `written`/`vote` hits have `senedd_tv_url: null` and `speech_id: null`;
  citations fall back to `source_id`.

### 1.2 Written only
**INPUT:**
```
Search only written answers for "cashless bus payments".
```
**EXPECT:** only `source_type: "written"` hits; surfaces the Bayliss → First
Minister pair. No speeches or votes.

### 1.3 Votes only
**INPUT:**
```
Find votes about the "Welsh Conservatives debate on the Welsh Government".
```
**EXPECT:** only `source_type: "vote"` hits, drawn from meeting `15798` /
agenda `260325-10`. Each has no speaker and no SeneddTV URL.

### 1.4 Source is reported, not guessed
**INPUT:**
```
Search for "local government funding pressures" and tell me, for each result,
whether it is a written answer or a spoken speech.
```
**EXPECT:** the model distinguishes sources using the `source_type` field rather
than inferring from content.

---

## 2. Vote tools

### 2.1 List votes
**INPUT:**
```
Find all recorded votes in the corpus and summarise their results.
```
**EXPECT:** `find_votes` returns 5 votes (motion + 4 amendments), all marked
rejected, with For/Against/Abstain tallies.

### 2.2 Vote detail
**INPUT:**
```
Get the full detail of the vote on the Welsh Conservatives motion — the tallies
and how each member voted.
```
**EXPECT:** `get_vote` returns motion metadata, tallies, EN/CY result, and a
per-member breakdown. All four outcomes can appear: **For / Against / Abstain /
DidNotVote**. Values are friendly strings, never `VoteResultEnum.FOR`.

### 2.3 Member voting record
**INPUT:**
```
Pick any member who took part in that vote and show me their full voting record.
```
**EXPECT:** `get_member_voting_record` lists that member's votes with each
result as `For` / `Against` / `Abstain` / `DidNotVote`.

---

## 3. Written answers

### 3.1 By meeting
**INPUT:**
```
Show me the written questions and answers from meeting 16060.
```
**EXPECT:** `get_written_answers` returns the content as **question→answer
pairs** (matched by `pair_id`), not loose rows. Questions carry a speaker name;
answers are attributed by job title (e.g. "First Minister") with no member id.

### 3.2 By topic/speaker
**INPUT:**
```
What written answer did the First Minister give about local government finances?
```
**EXPECT:** returns the Tom Montgomery → First Minister exchange; answer text
quoted with attribution.

---

## 4. Rhetoric ↔ vote bridge

### 4.1 Votes for a debate
**INPUT:**
```
Get the votes linked to the speeches on agenda item 260325-10 in meeting 15798.
```
**EXPECT:** `get_votes_for_speech` (or the meeting+agenda path) returns the votes
that share that meeting and agenda item — connecting the debate's speeches to the
divisions taken on it.

---

## 5. Prompt-driven, agent-style tasks

### 5.1 stance_vs_vote
**INPUT:**
```
Use the stance_vs_vote prompt: compare what was said in the Welsh Conservatives
debate with how members actually voted.
```
**EXPECT:** the model searches spoken speeches for the debate, pulls the matching
votes, and contrasts rhetoric with the recorded division. Every claim cited.

### 5.2 issue_briefing
**INPUT:**
```
Use the issue_briefing prompt to brief me on "bus services" in the Senedd.
```
**EXPECT:** a briefing that draws on speeches, written answers, **and** votes,
each clearly attributed.

### 5.3 compare_speakers
**INPUT:**
```
Use the compare_speakers prompt to compare two members from the members roster on
an issue of your choice.
```
**EXPECT:** resolves both members via `senedd_find_member`, searches per speaker,
and contrasts their positions with citations.

---

## 6. Edge cases & guardrails

### 6.1 Speaker filter on votes
**INPUT:**
```
Search votes for "NHS waiting times" by speaker "Jones".
```
**EXPECT:** **no vote results** (votes have no speaker, so the filter excludes
them) — a clean empty result, not an error.

### 6.2 Invalid source
**INPUT:**
```
Search the record for "housing" with source = "podcast".
```
**EXPECT:** a clean validation error naming the allowed values
(`spoken | written | vote`), not a stack trace.

### 6.3 Unknown id
**INPUT:**
```
Get vote 99999.
```
**EXPECT:** a friendly "not found" message, not an exception.

### 6.4 Resources reflect new coverage
**INPUT:**
```
Read senedd://corpus-stats and senedd://data-dictionary.
```
**EXPECT:** stats include date range + active embedding model; the data
dictionary describes votes and written QNR as available sources (no longer listed
as "not yet ingested").

---

## Pass criteria summary

- [ ] Cross-source search interleaves results and labels each `source_type`.
- [ ] `written`/`vote` hits cite `source_id` (SeneddTV URL / `speech_id` null).
- [ ] Enum values render as friendly strings (For/Against/Abstain/DidNotVote;
      question/answer).
- [ ] Written answers come back as Q&A pairs.
- [ ] Vote tools, member voting record, and the rhetoric↔vote bridge all return
      data for meeting `15798` / agenda `260325-10`.
- [ ] Speaker filter excludes votes; invalid source errors cleanly.
- [ ] Resources describe votes/QNR as ingested.
