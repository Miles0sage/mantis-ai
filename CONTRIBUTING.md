# Contributing to MantisAI

## Quick Setup

```bash
git clone https://github.com/Miles0sage/mantis-ai.git
cd mantis-ai
pip install -e ".[dev]"
pytest -q
```

## Guidelines

- **Keep changes focused.** One fix or feature per PR.
- **Include a test.** If you add a tool, adapter, or core behaviour, add a test for it.
- **Match the style.** Small files, clear names, async throughout.

## Good First Areas

- Richer task-tree UX in the web dashboard
- Stronger verifier reporting
- New model adapter (any OpenAI-compatible provider)
- End-to-end browser tests
- Demo scripts and GIFs

## Running Tests

```bash
pytest -q            # full suite
pytest tests/test_core.py -v   # specific file
```

## Submitting

Open a PR against `main`. Describe what you changed and why. Reference any related issue.
