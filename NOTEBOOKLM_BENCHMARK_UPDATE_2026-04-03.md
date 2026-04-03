# Mantis Benchmark Update — 2026-04-03

## Current State

Mantis now has:

- background jobs
- persistent sessions
- hard budget limits
- approval queue
- diff previews for file edits
- resume from paused approval checkpoints
- activity feed
- explicit strong-model escalation approval for background jobs

Local test suite status:

- `129 passed`

## Live Benchmark Findings

### Task 1: strict code generation

Prompt shape:

- create `token_bucket.py`
- implement `TokenBucket`
- exact API:
  - `__init__(capacity: int, refill_rate: float)`
  - `allow(tokens: int = 1) -> bool`
  - `available() -> float`
- create `check_token_bucket.py`
- do not run the check

Observed result:

- generated files exist
- API shape mostly correct
- checker fails on behavior:
  - expected `available()` to report `1.0` immediately after consuming two tokens from a fresh capacity-3 bucket
  - implementation refilled based on real elapsed time and returned a value slightly above `1.0`

Interpretation:

- this is an exactness / verifier problem, not a broad inability to write code
- the model produced a plausible implementation, but not one that matches the implied deterministic test expectations

### Task 2: multi-step file creation

Prompt shape:

- create `binary_search.py`
- create `test_binary_search.py`
- do not run pytest

Observed result:

- generated files are good
- external verification passes:
  - `10 passed`
- but the foreground `_run_chat()` request sometimes does not return cleanly even after files are already created

Interpretation:

- core code generation is acceptable here
- there is still a foreground lifecycle / completion-path hang on some successful generation tasks

### Task 3: surgical edit

Prompt shape:

- fix only an existing `calc.py`
- do not run tests

Observed result:

- passed cleanly
- external verification:
  - `1 passed`

### Task 4: background approval / resume

Prompt shape:

- edit file in background job
- approval required
- resume same job after approval

Observed result:

- passed end to end
- paused for approval
- resumed same job
- file changed correctly

## Honest Summary

Mantis is now good on:

- background approvals
- checkpoint resume
- visible async execution
- product shape

Mantis is still weak on:

1. strict deterministic codegen correctness when the “obvious” implementation has subtle behavior mismatches
2. foreground completion reliability when generation succeeds but the request does not finish cleanly

## Question For NotebookLM

Using the Claude Code leak sources and the other coding-agent sources already in this notebook:

1. What exact prompt / verifier / execution changes should Mantis make to fix the TokenBucket-style exactness failure?
2. What are the most likely architectural causes of a foreground request hang after files are already written successfully?
3. What is the smallest next implementation plan, in order, to improve benchmark reliability without bloating the product?
