# Thin cross-language task runner for the monorepo. No build system —
# each target is the one canonical command, documented in ARCHITECTURE.md.

.PHONY: sync test lint provision mcp mcp-http web dev fidelity eval

# Python -------------------------------------------------------------------

sync:            ## incremental data sync (acquisition + transform + embed sweep)
	uv run python main.py

test:            ## full offline test suite (mocked; no DB/GPU)
	uv run pytest tests/ -q

lint:            ## errors-only lint (same gate as CI)
	uvx ruff check --select E9,F63,F7,F82 .

provision:       ## migrate DATABASE_URL to head + register SQL procedures
	uv run python -c "import os; from senedd_data.provisioning import Provisioner; Provisioner(os.environ['DATABASE_URL']).create_schema()"

fidelity:        ## transcript-fidelity QA pass (run after ingest/reprocess)
	uv run python -m senedd_data.fidelity

eval:            ## retrieval eval scoreboard against the live DB
	uv run python -m tests.eval.runner

mcp:             ## MCP server on stdio (what an MCP client launches)
	uv run python -m senedd_mcp

mcp-http:        ## MCP server over streamable HTTP (for the web app / remote clients)
	uv run python -m senedd_mcp --transport streamable-http

# Web ----------------------------------------------------------------------

web:             ## Next.js dev server (apps/web)
	pnpm --filter @senedd/web dev

# Combined -----------------------------------------------------------------

dev:             ## DB-backed tool development: MCP over HTTP + web dev server
	$(MAKE) -j2 mcp-http web
