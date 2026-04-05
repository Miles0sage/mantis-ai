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
- `python -u scripts/curated_live_benchmark.py --loops 1 --budget 0.35 --timeout 90`
- `python scripts/full_validation_pack.py --stress-loops 1 --skip-curated`
- `python -u scripts/curated_live_benchmark.py --loops 1 --budget 0.35 --timeout 120`

## Headline Results

- Full test suite: `233 passed`
- Targeted routing/streaming suite: `112 passed`
- Scenario benchmark: `21/21` passed
- Scenario benchmark with server flows: `23/23` passed
- Stress benchmark: `50/50` loops green, `average_pass_rate: 1.0`
- Live benchmark: 4/4 tasks completed successfully with verification
- Curated real-provider benchmark: initial `9/10` passed
- Expanded curated real-provider benchmark: first rerun `18/20` passed, later rerun `19/20` passed
- Current curated real-provider benchmark at the Week 4 timeout budget: `19/20` passed
- Validation-pack smoke run: green via `scripts/full_validation_pack.py`

## Agency Upgrades Added

- Coordinator workers can now run in isolated git worktrees for multi-file orchestrated tasks.
- Worker metadata is exposed in execution stats, jobs, traces, and the dashboard.
- `run_bash` now supports an explicit `cwd`, and worker runs default it to the assigned project/worktree.
- Identical tool-call suppression now stops repeated loops earlier.
- Traces now record top-level execution mode, task type, and verifier verdict for filtering.

## Latency Notes

- Local fast-path reads are effectively immediate.
- Deterministic single-file return-value edits are now eligible for a local fast path and complete immediately in the sampled run.
- Real provider generation/edit tasks still work, but they remain the slowest part of the system.
- The curated live harness now emits per-scenario progress and enforces a per-scenario timeout, so long-tail failures show up as bounded misses instead of freezing the entire run.
- A full validation-pack entrypoint now exists at `scripts/full_validation_pack.py`.
- Observed real-provider timings from the sampled runs:
  - simple bugfix: about 14s
  - simple deterministic file edit: about 0s via local fast path
  - multi-file test-writing/generation: about 35s
  - many bounded bugfix/refactor/edit tasks: about 16-32s
  - slow refactor/import-export cases: about 59-79s
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
- The expanded curated real-provider set initially exposed two concrete misses in the 20-scenario run: `api_contract_generation` failed verification and `nested_service_fix` failed test collection.
- After fixing the `nested_service_fix` fixture shape, the next full rerun improved to `19/20`; the remaining concrete miss is `api_contract_generation`.
- Even with the verifier fix and a `120s` outer timeout, `api_contract_generation` still occasionally times out on the repair path and remains the main live miss in the curated set.
- Some orchestrated/refactor paths still return weak summaries like `Task completed after N iterations`, which is acceptable only because external verification caught the truth afterward.
- Latency on model-backed paths is still the main production blocker, even though the harness now reports progress instead of hanging silently.
- Confidence is strongest on bounded tasks and benchmark fixtures, not on arbitrary open-ended repo surgery.
- The next quality frontier is broader live edit/refactor coverage and further latency reduction on model-backed paths.

## Week 3 Memory Signal

- Repeating the previously failing `api_contract_generation` task in isolation twice produced two passing runs.
- Sample timings:
  - first isolated rerun: `27.41s`, `7 passed`
  - second isolated rerun: `25.75s`, `6 passed`
- That is evidence that the remaining miss is flaky rather than fundamentally broken, but it is not yet strong enough to claim the resume/memory path consistently repairs the failure case.
