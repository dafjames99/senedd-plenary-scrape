# Senedd Plenary MCP server

Exposes the `src/search` retrieval service as MCP tools, resources, and prompts.
All tools are read-only over the fixed corpus.

## Run

```bash
# Local (stdio) — what an MCP client launches
uv run python -m src.mcp_server

# Remote (HTTP) service
uv run python -m src.mcp_server --transport streamable-http
```

The server reads `DATABASE_URL` and the embedding settings from `.env`, and
embeds queries with the **active** `EMBEDDING_MODEL` — this must match the model
the corpus was embedded with (currently `ollama/embeddinggemma:300m` locally).

## Register with a client (stdio)

Claude Code (`.mcp.json` in the project, or `claude mcp add`):

```json
{
  "mcpServers": {
    "senedd": {
      "command": "uv",
      "args": ["run", "python", "-m", "src.mcp_server"],
      "cwd": "/absolute/path/to/senedd-scrape"
    }
  }
}
```

Claude Desktop uses the same shape under `mcpServers` in its config file.

## Tools

| Tool | Purpose |
|---|---|
| `senedd_search_speeches` | Semantic search; topic in `query`, constraints in filter fields |
| `senedd_get_speech` | Full text + context for one `speech_id` |
| `senedd_filter_speeches` | Structured (non-semantic) listing by speaker/member/date/agenda |
| `senedd_find_member` | Resolve a name → candidate `member_id`s (call before speaker filtering) |
| `senedd_get_member` | Member profile, role history, speech volume |
| `senedd_list_meetings` | Meetings with speech counts |
| `senedd_get_meeting` | One meeting + its agenda items |
| `senedd_get_agenda_thread` | Ordered conversation for an agenda item (recovers replies that omit the question's keywords) |

## Resources

- `senedd://data-dictionary` — what the corpus holds and how to query it well
- `senedd://corpus-stats` — live counts, date range, active embedding model
- `senedd://members` — full member roster with speech counts

## Prompts

- `senedd_search_strategy` — how to answer a question with filters + citations
- `senedd_position_over_time` — trace one member's evolving stance on an issue

## Notes / follow-ups

- Outputs are JSON (the consumer is an LLM that must cite exact `speech_id`s and
  URLs). Listings carry excerpts; fetch full text with `senedd_get_speech`.
- Each call currently constructs a DB engine via the service layer. Fine for
  local single-user stdio use; **centralise connection pooling before deploying
  the HTTP transport** for multiple clients.
- End-to-end (LLM-in-the-loop) evaluation of the tools is a later step — see
  `PLAN.md` Phase 5 and the eval discussion.
