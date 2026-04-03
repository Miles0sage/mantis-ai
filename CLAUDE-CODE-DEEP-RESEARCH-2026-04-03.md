# Claude Code Deep Research Memo — 2026-04-03

Notebook target:

- `MantisAI — Agentic Framework Build`
- id: `23b01309-51b9-4d62-b8b2-9b520f4806e7`

## What matters technically

### 1. Prompt architecture is modular, not monolithic

Public reverse-engineering writeups describe Claude Code as assembling the system prompt from many composable pieces rather than one giant static prompt. The most important pattern is a dynamic boundary split:

- static rules and behavioral instructions above the boundary
- session-specific material below it
- static prefix cached globally
- dynamic section injected per session

This matters for Mantis because it gives a path to:

- stronger prompts without paying full prompt cost every turn
- stable reusable policy text
- cheaper swarm / fork agents through shared cacheable prefixes

### 2. Prompt wording is treated like a product surface

The strongest analysis suggests Claude Code A/B tests exact wording:

- short tool-call chatter constraints
- anti-hallucination checks
- internal-only stricter variants
- model-launch annotations and prompt experiments

Implication for Mantis:

- stop treating the prompt as one-time prose
- version prompt modules
- test changes against benchmark tasks
- keep strict prompt metrics tied to completion quality and cost

### 3. Approval mailbox pattern is central to safe multi-agent work

Claude Code’s reported coordination pattern routes dangerous actions from workers to a coordinator mailbox instead of letting all workers execute directly.

This maps directly onto the strongest current Mantis product direction:

- background jobs
- approval queue
- diff preview
- resume after approval

For Mantis, the next leap is:

- multiple workers can research / edit in parallel
- only coordinator can commit risky actions
- approval queue remains single source of truth

### 4. Swarm design is not “many agents everywhere”

The useful breakdown from the research is:

- `fork`: same context, cheap due to shared prompt prefix
- `teammate`: shared workspace, less isolation
- `worktree`: isolated code workspace for parallel edits

Implication for Mantis:

- do not build open-ended swarms
- build 2 or 3 agent roles with clear ownership
- cheapest useful v1 is:
  - planner/coordinator
  - worker for code task
  - verifier/reviewer

### 5. Verification should be adversarial, not just “did tests pass”

One reverse-engineering source claims an internal verification agent is used for non-trivial work.

For Mantis this suggests:

- add a verification stage for substantial edits
- verifier should challenge the result
- verify files changed, tests, and prompt requirement adherence

This is especially needed because the live benchmark exposed failures where output looked plausible but did not satisfy the requested interface.

### 6. Persistent autonomy only works with strong constraints

KAIROS-style ideas in the public analyses are useful, but only if paired with:

- append-only logs
- bounded background runtime
- strict approvals
- hard budget ceilings

Mantis should not chase full autonomy first. The product wedge remains:

- visible async execution
- cheap-by-default routing
- human approval for risky work

## What Mantis should steal

- prompt modules with static/dynamic boundary
- cache-aware forked worker architecture
- mailbox approval pattern for worker actions
- explicit verifier agent for meaningful changes
- append-only activity log for background/autonomous work
- worktree-based isolation for parallel edits

## What Mantis should not steal yet

- always-on daemon autonomy
- giant undocumented feature-flag forest
- hidden internal-only product behavior
- broad swarm orchestration before coordination is robust

## Benchmark-grounded product lessons

Recent live Mantis checks showed:

- live DeepSeek API path works
- direct `/api/chat` works
- task-style coding can pass on binary search + tests
- simple surgical bug fix can pass
- codegen with strict interface adherence is still weak
- background approval flow has a real stuck-in-running bug through the server path

So the practical next product step is:

1. fix background job stuck/running bug
2. tighten prompt + verification on exact interface adherence
3. add coordinator/worker/verifier mode with approval mailbox

## Best evolutionary product direction

Mantis should evolve into:

`A cheap-by-default async coding agent with coordinator/workers, visible approvals, verifier-backed completion, and browser-first control.`

Not:

- “Claude Code clone”
- “swarm of agents doing everything”
- “always-on autonomous daemon” yet

## Concrete next architecture

### Prompt stack

- static global core prompt
- dynamic repo/session instructions
- role-specific prompt modules:
  - coordinator
  - worker
  - verifier

### Agent stack

- coordinator decides plan and ownership
- workers do bounded tasks in isolated scope
- verifier checks requirement match, changed files, and tests

### Safety/control stack

- all risky actions go through mailbox approvals
- all file-changing actions can show diff preview
- all resume paths continue from checkpoint, not full restart
- all background work writes append-only activity events

## Questions for NotebookLM

- what is the best minimal coordinator/worker/verifier design for Mantis?
- which Claude Code patterns are highest leverage and lowest complexity for an open-source product?
- what launch message makes this feel like the next step after terminal coding agents?
