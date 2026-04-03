# MantisAI

**Cheap-by-default async coding agent with visible approvals, verifier-backed completion, and browser-first control.**

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-134%20passing-brightgreen)](tests/)

MantisAI is a self-hosted coding agent built for cheap models first. It runs tasks in the background, pauses for approvals on risky actions, resumes from checkpoints, shows cost and execution state in a web dashboard, and verifies generated artifacts before calling a task done.

Point it at OpenAI-compatible providers like DeepSeek, Qwen, OpenAI, or local endpoints. No vendor lock-in.

---

## 30-Second Quickstart

```bash
pip install mantisai

export MANTIS_API_KEY=sk-your-key
export MANTIS_MODEL=gpt-4o-mini   # or deepseek-chat, qwen-plus, etc.

mantisai chat
```

That's it. You're running an async coding agent with routing, approvals, budgets, and a browser dashboard built in.

---

## Why Mantis

- **Cheap by default** — route simple work to low-cost models and escalate only when needed
- **Background jobs** — queue work, come back later, and resume from checkpoints
- **Visible approvals** — risky commands and edits pause for review with previews and diffs
- **Verifier-backed completion** — generated checks and tests are used as gates, not just narrative output
- **Browser-first control** — task tree, activity feed, cost meter, approvals, and job history in one UI
- **Budget limits** — hard spend ceilings stop runs before they drift
- **Project context** — `MANTIS.md` provides repo-specific rules and standards

---

## Supported Models

| Model | Input | Output | Notes |
|---|---|---|---|
| `gpt-4o-mini` | $0.00015/1K | $0.00060/1K | Good default, fast |
| `deepseek-chat` | $0.00027/1K | $0.00110/1K | Strong coding, cheap |
| `qwen-plus` | $0.00040/1K | $0.00120/1K | Alibaba, very cheap |
| `claude-3-5-sonnet` | $0.00300/1K | $0.01500/1K | Best quality ceiling |
| Any OpenAI-compatible | varies | varies | Set `MANTIS_BASE_URL` |
| Ollama (local) | free | free | Set base URL to localhost |

Swap models without changing anything else:

```bash
export MANTIS_MODEL=deepseek-chat
export MANTIS_BASE_URL=https://api.deepseek.com/v1
mantisai run "Refactor this module for clarity"
```

---

## CLI Commands

```bash
mantisai chat                          # interactive agentic session
mantisai run "fix the type errors"     # single-shot prompt
mantisai serve                         # launch streaming web UI on :8000
mantisai models                        # list configured models and costs
mantisai tools                         # list available tools
```

---

## How It Works

- **Plan** — Mantis builds an execution plan from the prompt and detects file targets and complexity
- **Route** — cheap models handle easy work; stronger models are reserved for costlier or riskier tasks
- **Execute** — the agent uses file, search, and shell tools to complete the task
- **Verify** — generated check files and tests are run as artifact gates where applicable
- **Pause and Resume** — risky actions enter the approval queue and resume the same job after review
- **Track** — jobs, plans, cost, approvals, and activity are visible in the dashboard

---

## MANTIS.md

Drop a `MANTIS.md` file in your project root. MantisAI reads it at session start and uses it as persistent project context — coding standards, architecture decisions, things to avoid, preferred tools.

```bash
# in your project root
cat MANTIS.md
```

```markdown
# My Project

Python 3.11+. FastAPI backend. No ORMs — raw SQL with psycopg3.
Tests live in tests/. Run with pytest -q.
Never use print() — use the logger at src/logger.py.
```

Now every session in that directory starts with that context loaded. No repeating yourself.

---

## Web UI

```bash
mantisai serve
# open http://localhost:8000
```

The dashboard includes:

- streaming task tree
- background jobs
- approval queue
- activity feed
- hard budget display
- verifier and artifact-check summaries

---

## Contributing

```bash
git clone https://github.com/Miles0sage/mantis-ai.git
cd mantis-ai
pip install -e ".[dev]"
pytest -q
```

Good next areas: richer task-tree UX, stronger verifier reporting, better launch/demo polish, and end-to-end browser tests.

PRs welcome. Keep changes focused. If you're adding a new adapter or tool, include a test.

---

## License

MIT. See [LICENSE](LICENSE).
