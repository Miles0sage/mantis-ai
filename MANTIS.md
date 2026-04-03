# MantisAI — Project Instructions

This file is loaded by MantisAI at session start when you run `mantisai chat` or `mantisai run`
from this directory. Edit it to match your project.

---

## Project

MantisAI is an open-source agentic coding engine. Python 3.11+. MIT license.

The core library lives in `mantis/`. CLI entry point is `mantis/cli.py`. Web server is
`mantis/server.py`. The query loop that drives all agent behavior is `mantis/core/query_engine.py`.

---

## Coding Standards

- Python 3.11+ only. Use type hints everywhere — no untyped function signatures.
- Immutable patterns preferred. Return new objects rather than mutating in place.
- Functions under 50 lines. Files under 400 lines. Split when you hit the limit.
- No `print()` in library code — raise exceptions or return error values.
- Error handling must be explicit. Never catch bare `Exception` without logging and re-raising.
- All async functions must be actually async (no sync blocking calls inside async functions).

---

## File Layout

```
mantis/
  cli.py          — CLI entry point (argparse, ANSI output)
  app.py          — MantisApp: config, model selection, tool loading
  server.py       — FastAPI web server, SSE streaming
  core/
    query_engine.py    — main agent loop
    model_adapter.py   — OpenAI-compatible API calls
    tool_registry.py   — tool registration and dispatch
    hooks.py           — pre/post tool hooks
    permissions.py     — tool permission scopes
    quality_gate.py    — output scoring and retry logic
    planner.py         — task planning
    ast_extractor.py   — AST-based context extraction
  tools/
    builtins.py        — read_file, write_file, edit_file, run_bash, glob_files, grep_search
    edit_applicator.py — diff/patch application logic
  memory/
    store.py           — persistent memory store
    search.py          — memory recall / semantic search
demos/               — standalone runnable demos
tests/               — pytest suite
```

---

## Preferred Tools and Libraries

- HTTP: `httpx` (already in deps). No `requests`.
- Config: environment variables + YAML. No `.env` files committed.
- Tests: `pytest` + `pytest-asyncio`. Test files mirror source layout in `tests/`.
- Web: `FastAPI` + `uvicorn`. Keep the server thin — business logic stays in `mantis/core/`.
- Parsing: use the stdlib `ast` module for Python AST work. No additional AST libraries.

---

## Things to Avoid

- Do not add new required dependencies without a strong reason. Keep the install light.
- Do not hardcode model names or API endpoints in library code — always read from config/env.
- Do not add vendor-specific logic to `query_engine.py` — that belongs in `model_adapter.py`.
- Do not break the OpenAI-compatible contract. All adapters must speak the same interface.
- Do not commit API keys, tokens, or any secrets. Use environment variables.
- Do not make the CLI interactive in ways that break `mantisai run` (non-interactive mode).

---

## Running Tests

```bash
pytest -q
```

All 20 tests should pass. If you add a feature, add a test. If you fix a bug, add a regression test.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `MANTIS_API_KEY` | Yes | API key for the model provider |
| `MANTIS_MODEL` | No | Model name (default: `gpt-4o-mini`) |
| `MANTIS_BASE_URL` | No | OpenAI-compatible base URL (default: OpenAI) |
| `MANTIS_MAX_TOKENS` | No | Max output tokens per call |
| `MANTIS_TEMPERATURE` | No | Sampling temperature (default: 0.2) |
