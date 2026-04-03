# Mantis Build Progress — 2026-04-03

## Product Direction

Mantis is being positioned as a self-hosted coding agent with:

- background jobs
- cheap-by-default model routing
- visible execution in a web dashboard
- hard budget controls
- persistent sessions and resume

## Implemented

- quality gate no longer loops on feature tasks
- non-interactive permission checks fail closed instead of crashing
- prompt-aware model routing:
  - cheap model for low-scope work
  - stronger model for cross-file or escalated work
- background jobs persisted to disk
- persisted session history and resume primitives
- hard budget USD cap in runtime adapter
- dashboard config field for budget
- streaming cost and remaining budget surfaced in UI

## Current Gaps

- no approval queue / review UI for risky tools
- no multi-file diff preview before edits
- no live activity feed for job lifecycle and key events
- no resumable execution from intermediate task checkpoints
- no explicit cost-aware escalation policy in the UI

## Current Recommendation Candidate

The next highest-value slice appears to be:

1. approval queue for risky actions
2. activity feed / progress log in dashboard
3. multi-file diff preview before apply

## Ask

Given the current implemented state and the Claude Code leak research sources already in this notebook:

- what should Mantis build next to become a real low-cost product that gets attention?
- what should be the next 3 implementation steps in order?
- what is the best feature wedge that still looks differentiated from Claude Code, OpenHands, and Aider?
