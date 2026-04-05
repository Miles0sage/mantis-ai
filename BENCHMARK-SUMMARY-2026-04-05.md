# Benchmark Summary

Date: 2026-04-05
Repo: `/root/mantis-ai`

## What Ran

- `pytest -q`
- `pytest -q tests/test_core.py tests/test_planner.py tests/test_agentic_loop.py tests/test_server_background.py`
- `python scripts/scenario_benchmark.py`
- `python scripts/scenario_benchmark.py --include-server`
- `python scripts/stress_benchmark.py --loops 50`
- `python scripts/live_benchmark.py --loops 1 --budget 0.25`
- `python scripts/curated_live_benchmark.py --loops 1 --budget 0.35`

## Headline Results

- Full test suite: `217 passed`
- Targeted routing/streaming suite: `95 passed`
- Scenario benchmark: `21/21` passed
- Scenario benchmark with server flows: `23/23` passed
- Stress benchmark: `50/50` loops green, `average_pass_rate: 1.0`
- Live benchmark: 4/4 tasks completed successfully with verification
- Curated real-provider benchmark: `9/10` passed

## Latency Notes

- Local fast-path reads are effectively immediate.
- Deterministic single-file return-value edits are now eligible for a local fast path and complete immediately in the sampled run.
- Real provider generation/edit tasks still work, but they remain the slowest part of the system.
- Observed real-provider timings from the sampled runs:
  - simple bugfix: about 14s
  - simple deterministic file edit: about 0s via local fast path
  - multi-file test-writing/generation: about 35s
  - some refactor/edit tasks: about 26-95s
  - one failing token-bucket generation run: about 182s before returning an incorrect implementation

## What Improved

- Simple read-only prompts no longer stall.
- Streamed chat no longer leaks pseudo-command narration.
- Health/config endpoints stay responsive while chat work is running.
- Planner no longer over-splits `read ... and reply ...` prompts.
- Identical repeated tool calls are suppressed.
- Streaming and blocking chat now share the same local fast-path behavior for simple file inspection.

## Remaining Weak Spots

- Model-backed tasks are still slower than they should be.
- The curated real-provider set exposed one concrete miss: `token_bucket` returned an implementation that failed the checker (`9/10` overall).
- Confidence is strongest on bounded tasks and benchmark fixtures, not on arbitrary open-ended repo surgery.
- The next quality frontier is broader live edit/refactor coverage and further latency reduction on model-backed paths.
