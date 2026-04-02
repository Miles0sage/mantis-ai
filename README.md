# MantisAI

> Agent OS for any OpenAI-compatible LLM.
> Route cheap models, give them real tools, and turn them into a coding agent.
> On low-cost providers, short tool loops can get close to $0.001/task.

```bash
git clone https://github.com/Miles0sage/mantis-ai.git
cd mantis-ai
pip install -e .
mantisai chat
```

## 30-Second Demo

Hero demo: scan Polymarket for mispriced contracts.

```text
$ python demos/prediction_market.py --limit 5

MantisAI — Prediction Market Scanner

Question                                                     Yes     No  Spread   Edge%  Kelly%    Rec
---------------------------------------------------------------------------------------------------------
Will ETH be above $4,000 by Friday?                       0.540  0.430   0.030    3.00%   3.09%  WATCH
Will the Fed cut rates in June?                           0.610  0.350   0.040    4.00%   4.17%  WATCH
Will Bitcoin hit a new ATH this month?                    0.470  0.470   0.060    6.00%   6.38%    BUY
---------------------------------------------------------------------------------------------------------
Scanned: 5 markets | Opportunities: 3 | Best edge: 6.00%
```

The core agent stack gives models built-in tools like `read_file`, `write_file`, `edit_file`, `run_bash`, `glob_files`, and `grep_search` through a single query loop.

## Why MantisAI

- **Any model**: point MantisAI at OpenAI, DeepSeek, Alibaba, Ollama, or another OpenAI-compatible endpoint.
- **Built-in tools**: file read/write/edit, bash, glob, and grep are already wired into the tool registry.
- **Agent core, not just chat**: the repo includes routing, hooks, memory, skills, and parallel-agent primitives.

## Quick Start

```bash
git clone https://github.com/Miles0sage/mantis-ai.git
cd mantis-ai
pip install -e .
export MANTIS_API_KEY=sk-your-key
export MANTIS_BASE_URL=https://api.openai.com/v1
export MANTIS_MODEL=gpt-4o-mini
mantisai chat
```

Run one prompt:

```bash
mantisai run "Summarize the current repository layout"
```

Inspect the configured surface:

```bash
mantisai models
mantisai tools
```

## Supported Models

Approximate costs are rough reference points for short tool-driven tasks and vary by prompt length and provider pricing.

| Provider | Example model | API style | Approx short task cost |
| --- | --- | --- | --- |
| OpenAI | `gpt-4o-mini` | native / compatible | `$0.001-$0.01` |
| DeepSeek | `deepseek-chat` | compatible | `$0.001-$0.005` |
| Alibaba | `qwen-plus` | compatible | `$0.001-$0.01` |
| Anthropic | `claude-3-5-sonnet` | adapter work needed | `$0.01-$0.10` |
| Ollama | `llama3` | local | hardware-bound |
| Custom | any compatible model | compatible | depends on endpoint |

## Architecture

```text
┌──────────────┐
│   CLI / API  │  mantisai chat | mantisai run
└──────┬───────┘
       │
┌──────▼───────┐
│  MantisApp   │  config, model selection, tool loading
└──────┬───────┘
       │
┌──────▼───────┐
│ QueryEngine  │  loop: model -> tool call -> model
└───┬────┬─────┘
    │    │
    │    └──────────────┐
    │                   │
┌───▼─────────┐   ┌─────▼─────┐
│ModelAdapter │   │ToolRegistry│
└─────────────┘   └─────┬─────┘
                        │
              ┌─────────▼─────────┐
              │ read/write/edit   │
              │ bash/glob/grep    │
              └─────────┬─────────┘
                        │
              ┌─────────▼─────────┐
              │ hooks / memory /  │
              │ skills / spawner  │
              └───────────────────┘
```

## Demos

- `python demos/prediction_market.py` — scan Polymarket for mispriced contracts.
- `python demos/lead_gen.py "AI startups"` — find leads and draft outreach from public web search.
- `python demos/sports_analytics.py` — scan for sports odds arbitrage.

See [demos/README.md](demos/README.md) for the full demo commands.

## Comparison

| Feature | MantisAI | Aider | Claude Code | Goose | Cline |
| --- | --- | --- | --- | --- | --- |
| Any OpenAI-compatible LLM | Yes | Partial | No | Partial | Yes |
| Built-in file + shell tools | Yes | Yes | Yes | Partial | Yes |
| Cost-aware routing primitives | Yes | No | No | No | No |
| Local demos outside coding | Yes | No | No | No | No |
| Hooks and memory modules | Yes | Limited | Session-only | Limited | Limited |
| Parallel agent primitives | Experimental | No | No | No | No |
| MIT license | Yes | Yes | No | Yes | Yes |

## What Is Real Today

- The core library is tested: `20 passed` in the current `tests/test_core.py` suite.
- Built-in tools, memory store, hooks, skills loader, and model adapter modules all exist in the repo.
- The CLI now has a working package entrypoint again.

## What Still Needs Work

- The CLI is a thin shell over the core library, not a mature product yet.
- The Anthropic row above reflects intended support direction; the current adapter is centered on OpenAI-compatible chat APIs.
- Agent spawning and advanced routing exist as primitives, but they still need deeper end-to-end integration.

## Contributing

```bash
git clone https://github.com/Miles0sage/mantis-ai.git
cd mantis-ai
pip install -e ".[dev]"
pytest -q
```

Small, concrete fixes are the fastest way to move this repo forward: packaging, adapters, demos, and real end-to-end tests.

## License

MIT. See [LICENSE](LICENSE).
