# Task Board

## Week 1: Latency And Stop Conditions

- [ ] Add per-task iteration budgets in `mantis/core/query_engine.py`
- [ ] Add early-exit behavior after successful read-only or single-edit tool results
- [ ] Track tool-loop counts and repeated pattern counts in execution metadata
- [ ] Record latency at task, worker, and run levels
- [ ] Extend benchmark output with median and slowest timings
- [ ] Run targeted tests
- [ ] Run server-inclusive scenario benchmark
- [ ] Run curated live/provider benchmark

## Week 2: Worker Isolation And Merge Discipline

- [x] Add isolated worker worktrees for orchestrated tasks
- [x] Capture worker-level changed files and diff previews
- [x] Detect overlapping file ownership before parallel spawn
- [x] Force serialization for overlapping-target worker sets
- [x] Surface worker diffs and ownership in dashboard and job payloads
- [ ] Add multi-worker scenario coverage
- [ ] Run focused orchestrator/worktree/server tests
- [ ] Run one real orchestrated temp-repo task

## Week 3: Memory, Resume, And Workflow Coverage

- [x] Build trace-derived retrieval for similar prompts/tasks
- [x] Store worker-level resume metadata
- [x] Add rerun-only-failed-worker flow
- [x] Expand curated live/provider scenarios by 10 more tasks
- [ ] Compare first-run vs resumed-run performance
- [x] Run trace/server/orchestrator tests
- [x] Run curated live/provider benchmark rerun

## Week 4: Launch Hardening And Product Proof

- [ ] Finalize before/after benchmark summary
- [x] Add one command to run the full validation pack
- [ ] Tighten docs around current strengths and limits
- [ ] Add release checklist and failure checklist
- [ ] Run final live/provider evaluation sweep
- [ ] Verify full suite, scenario, stress, and live checks
