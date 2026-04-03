# Mantis Next Steps — NotebookLM Grounded

Date: 2026-04-03
Notebook: `MantisAI — Agentic Framework Build`
Notebook ID: `23b01309-51b9-4d62-b8b2-9b520f4806e7`

## Best Next Feature Wedge

Build a cost-aware approval queue with visual diffs for background jobs.

This keeps Mantis differentiated as:

- self-hosted
- async/background-first
- cheap-by-default
- human-in-the-loop

## Next 3 Implementation Steps

1. Approval queue for risky tools and model-cost escalation
2. Multi-file diff preview before apply
3. Resume execution from the paused checkpoint after approval

## Defer

- always-on daemon behavior
- complex multi-agent swarms
- gimmicks / novelty UX
- enterprise plugin / auth layers

## Public Positioning

Mantis is:

`A self-hosted, open-source AI coding agent with real budget controls, cheap-by-default model routing, and a UI that doesn't suck.`

## Why This Order

The current product already has:

- background jobs
- persisted sessions
- task tree streaming
- prompt-aware routing
- hard budget limits

The missing product glue is trust:

- approval before risky changes
- visible diffs
- pause and resume through long tasks
