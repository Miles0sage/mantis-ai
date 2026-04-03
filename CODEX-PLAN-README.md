# MantisAI — Star-Worthy README Rewrite

## Context
MantisAI is at /root/mantis-ai. Current README.md exists but needs to be rewritten to drive GitHub stars.
The repo is live at github.com/Miles0sage/mantis-ai with 0 stars.

## Task
Rewrite README.md to be viral-worthy. Follow this exact structure:

### Section 1: Hero (first 5 lines people see)
```
# MantisAI — Agent OS for Any LLM

> Turn DeepSeek V3 into a coding agent for $0.001/task.
> Works with OpenAI, Anthropic, Ollama, Alibaba, MiniMax — any OpenAI-compatible API.

pip install mantisai && mantisai chat
```

### Section 2: 30-Second Demo
- Show a terminal screenshot or ASCII recording of MantisAI autonomously completing a task
- Use the prediction_market demo as the hero example
- Show the agent calling tools (read_file, run_bash, etc.) and producing output

### Section 3: Why MantisAI
Three bullet points max:
1. **Any model** — not locked to one provider. Use $0.001 models or $0.10 models.
2. **Built-in tools** — read, write, edit, bash, glob, grep. No plugins needed.
3. **Agent OS, not chatbot** — context compression, model routing, memory, hooks.

### Section 4: Quick Start (must be copy-pasteable)
```bash
pip install mantisai
export MANTIS_API_KEY=sk-your-key
export MANTIS_BASE_URL=https://api.openai.com/v1  # or any compatible API
mantisai chat
```

### Section 5: Supported Models (table)
Show 6-8 providers with model names and approximate cost per task.

### Section 6: Architecture (clean ASCII diagram)
Keep the existing one but simplify.

### Section 7: Demos
Link to demos/ folder with one-liner for each demo:
- `python demos/prediction_market.py` — scan Polymarket for mispriced contracts
- `python demos/lead_gen.py "AI startups"` — find and research leads
- `python demos/sports_analytics.py` — find arbitrage in sports odds

### Section 8: Comparison Table
MantisAI vs Aider vs Claude Code vs Goose vs Cline
Focus on: any LLM, cost routing, memory, agent spawning

### Section 9: Contributing + License
Short, standard, MIT.

## Rules
- No emojis unless absolutely necessary
- Keep total README under 200 lines
- Every code block must be copy-pasteable and work
- First impression matters — someone decides to star in 10 seconds
- The hook is COST: "$0.001 per task" must be prominent
