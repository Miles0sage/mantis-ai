# MantisAI — Project Instructions

## Project
Open-source agentic coding engine. Model-agnostic Claude Code alternative.
Repo: /root/mantis-ai/ | Tests: `cd /root/mantis-ai && python -m pytest -q`

## NotebookLM
Notebook ID: 23b01309-51b9-4d62-b8b2-9b520f4806e7 (MantisAI — Agentic Framework Build)
After EVERY /checkpoint or /cw: call mcp__google-research__notebooklm_add_text with notebook_id above, title = "Checkpoint [date]", text = full checkpoint content. This keeps the notebook in sync so any session can resume from there.

## Architecture Status
- Inner tool loop (QueryEngine.run): WIRED, works
- run_agentic (planner + quality gate): WIRED, works
- SystemPrompt: built, NOT wired — must pass to QueryEngine
- HookManager: built, NOT wired
- PermissionManager: built, NOT wired
- AgentSpawner: built, NOT wired
- MemoryStore/Search: built, NOT wired (+ list_keys() bug → should be list_all())
- EditApplicator: built, NOT wired
- Web UI sessions: stateless per request — needs session_id dict

## Wiring Order (do not skip ahead)
1. system_prompt → query_engine.py
2. session state → server.py
3. memory tools → builtins.py + fix list_keys bug
4. hooks + permissions → tool dispatch
5. context manager + compressor
6. agent spawner (parallel tasks)
7. edit applicator

## Rules
- Run tests after every change: `python -m pytest -q`
- AI Factory is PRIVATE — never reference worker dispatch or cost routing in this repo
- Keep builtins.py tools simple, no internal AI Factory patterns
