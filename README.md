<!-- README.md -->

# MantisAI

**Open source agent OS that turns any LLM into a coding agent**

```
pip install mantisai
export MANTIS_API_KEY=sk-...
mantisai chat
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                           CLI                                │
│                     mantisai chat                            │
└──────────────────────────┬──────────────────────────────────┘
                           │
                    ┌──────▼──────┐
                    │ QueryEngine │
                    └──────┬──────┘
                           │
              ┌────────────┴────────────┐
              │                         │
       ┌──────▼──────┐          ┌──────▼──────┐
       │ModelAdapter │          │ HookManager │
       └──────┬──────┘          └─────┬───────┘
              │                       │
    ┌─────────┴─────────┐     ┌───────▼────────┐
    │                   │     │                │
    ▼                   ▼     ▼                ▼
 GPT-4o            Claude   Pre     Post      Memory
 DeepSeek          Ollama   Hook    Hook
 Qwen              Custom   ▲        ▲
```

## Key Features

- **Any Model**: Works with OpenAI-compatible APIs, local models via Ollama, Claude via Anthropic API
- **Built-in Tools**: `read`, `write`, `edit`, `bash`, `glob`, `grep` — no extra setup
- **Skills System**: Extend capabilities with custom skill modules
- **Hooks**: Pre/post processing at query level for logging, filtering, auth
- **Memory**: Persistent context across sessions with semantic search
- **Agent Spawning**: Fork sub-agents for parallel task execution
- **Cost-Aware Routing**: Automatically selects cheapest capable model for each task

## Quick Start

```bash
pip install mantisai

export MANTIS_API_KEY=sk-your-key
# or for Anthropic (Claude):
export ANTHROPIC_API_KEY=sk-ant-...

mantisai chat
```

Single-file edit:
```bash
mantisai edit src/main.py --instruction "add error handling"
```

Run a task:
```bash
mantisai run --task "refactor the auth module"
```

## Supported Models

| Provider | Models |
|----------|--------|
| OpenAI | GPT-4o, GPT-4o-mini, GPT-4, o1-preview, o1-mini |
| Anthropic | Claude 3.5 Sonnet, Claude 3 Opus, Claude 3 Haiku |
| DeepSeek | DeepSeek Chat, DeepSeek Coder |
| Alibaba | Qwen 2.5, Qwen Max |
| MiniMax | MiniMax Text-01 |
| Ollama | Any local model (llama3, codellama, mistral, etc.) |
| Custom | Any OpenAI-compatible endpoint |

Set via environment variable:
```bash
export MANTIS_MODEL=anthropic/claude-3-5-sonnet-20241022
export OPENAI_BASE_URL=https://api.deepseek.com/v1  # for compatible APIs
```

## Comparison

| Feature | MantisAI | Aider | Claude Code | Goose | Cline |
|---------|----------|-------|-------------|-------|-------|
| Any LLM | Yes | Limited | Claude only | Limited | Yes |
| Local models | Yes | Yes | No | Yes | Yes |
| Built-in tools | Yes | Yes | Yes | Basic | Yes |
| Skills system | Yes | No | No | No | Extension |
| Memory | Yes | No | Session | Limited | No |
| Hooks | Yes | No | No | No | No |
| Agent spawning | Yes | No | No | No | No |
| Cost routing | Yes | No | No | No | No |
| License | MIT | MIT | Proprietary | MIT | MIT |

## Configuration

```yaml
# ~/.mantisai/config.yaml
model: anthropic/claude-3-5-sonnet-20241022
max_tokens: 4096
temperature: 0.7
tools:
  - read
  - write
  - edit
  - bash
  - glob
  - grep
hooks:
  pre:
    - myhooks.authenticate
  post:
    - myhooks.log_response
memory:
  provider: sqlite
  path: ~/.mantisai/memory.db
```

## Skills

Create `~/.mantisai/skills/my_skill.py`:

```python
from mantisai import skill

@skill(name="review", description="Code review with style guide")
def code_review(file_path: str) -> str:
    """Review a file and return suggestions."""
    # implementation
    return "Found 3 issues..."
```

Use in conversation:
```
/skill review src/app.py
```

## Contributing

Contributions welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

```bash
git clone https://github.com/mantisai/mantisai
cd mantisai
pip install -e ".[dev]"
pytest tests/
```

- Read [ARCHITECTURE.md](ARCHITECTURE.md) for design docs
- Join [Discord](https://discord.gg/mantisai) for discussion
- Check [Good First Issues](https://github.com/mantisai/mantisai/labels/good%20first%20issue)

## License

MIT License. See [LICENSE](LICENSE).

---

 Built for developers who want control over their agent stack.