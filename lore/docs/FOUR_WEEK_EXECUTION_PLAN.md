# Four Week Execution Plan

## Goal

Ship Mantis from "credible and improving" to "reliably useful for bounded coding work" by focusing on latency, worker isolation, observability, and live-task evaluation.

This plan assumes Mantis is the system executing the work. Each week ends with real benchmark runs, not just unit-test completion.

## Success Criteria

By the end of week 4, Mantis should:

- complete bounded read/edit/refactor tasks reliably
- keep server health responsive during active jobs
- expose worker ownership, worktree state, and verifier outcomes clearly
- pass the synthetic benchmark suite consistently
- pass a broader curated live/provider benchmark set with materially better latency

Target outcome:

- `pytest -q` green
- `python scripts/scenario_benchmark.py --include-server` green
- `python scripts/stress_benchmark.py --loops 50` green
- curated live/provider benchmark at `>= 9/10` with lower tail latency than current baseline

## Operating Rules

- Every implementation step must end with runnable verification.
- Prefer temp repos or temp task directories for live edits before touching real repo workflows.
- Use real provider runs every week, not only mocked/unit coverage.
- Track regressions in benchmark summary docs instead of smoothing them over.

## Week 1: Latency And Stop Conditions

### Objective

Reduce wasted model/tool loop time on simple and medium-complexity tasks.

### Deliverables

- tighten stop conditions after successful tool results
- add task-type-specific max iteration budgets
- add early-exit logic when artifact checks or postconditions already pass
- reduce repeated read/search/tool churn
- add benchmark timing capture per task and per worker

### Implementation Tasks

1. Add per-task iteration budgets in `mantis/core/query_engine.py`.
2. Add early final-answer prompting after successful read-only or single-edit tool results.
3. Track tool-loop counts and repeated pattern counts in execution metadata.
4. Record latency at task, worker, and full-run levels in traces and job metadata.
5. Extend benchmarks to report median and slowest task durations.

### Verification

- `pytest -q tests/test_agentic_loop.py tests/test_core.py tests/test_server_background.py`
- `python scripts/scenario_benchmark.py --include-server`
- one real `/api/chat` and one `/api/chat/stream` run on a read/edit task
- one curated live/provider run

### Exit Gate

- no regression in scenario benchmark pass rate
- simple read/edit tasks finish immediately or near-immediately on local fast paths
- model-backed bounded tasks show lower average completion time than current baseline

## Week 2: Worker Isolation And Merge Discipline

### Objective

Make multi-worker execution safer and more reviewable.

### Deliverables

- isolated worker worktrees fully integrated into orchestrated flows
- explicit worker ownership over files/tasks
- diff collection per worker
- worker result bundle including changed files, branch, cost, duration, verifier outcome

### Implementation Tasks

1. Extend orchestrator metadata to store worker diffs and changed files.
2. Add worker-level git review collection after task completion.
3. Add worker conflict detection for overlapping file ownership.
4. Add orchestrator policy: avoid parallel execution for overlapping targets.
5. Surface worker diffs and ownership in dashboard and job payloads.

### Verification

- `pytest -q tests/test_orchestrator.py tests/test_worktree_manager.py tests/test_server_serialization.py`
- add multi-worker scenario coverage to `scripts/scenario_benchmark.py`
- run one real orchestrated multi-file task in a temp git repo

### Exit Gate

- orchestrated tasks produce inspectable worker branches/worktrees
- overlapping-target tasks are serialized or rejected intentionally
- dashboard shows enough worker detail to debug failures

## Week 3: Memory, Resume, And Real Workflow Coverage

### Objective

Make long-running work resumable and informed by prior runs.

### Deliverables

- trace-derived memory for repeated task patterns
- richer resume checkpoints
- rerun-only-failed-worker capability
- curated live benchmark expanded with more real edit/refactor tasks

### Implementation Tasks

1. Build small memory retrieval layer from traces for similar prompts/tasks.
2. Store worker-level checkpoint state and resume metadata.
3. Add "resume failed worker only" flow in orchestrator/background jobs.
4. Expand curated live/provider scenarios by at least 10 more bounded tasks.
5. Add benchmark summaries comparing first-run vs resumed-run performance.

### Verification

- `pytest -q tests/test_trace_store.py tests/test_server_background.py tests/test_orchestrator.py`
- background approval/resume scenarios
- curated live/provider benchmark rerun

### Exit Gate

- resumed jobs do not redo already-completed safe work
- trace filtering and retrieval work by execution mode and verdict
- expanded curated set remains stable enough to trust as a release signal

## Week 4: Launch Hardening And Product Proof

### Objective

Turn the engineering gains into a repeatable "this works" proof.

### Deliverables

- benchmark report for current state vs baseline
- final dashboard/job/traces polish
- release checklist for bounded-task readiness
- documented known limits for model-backed latency and open-ended repo surgery

### Implementation Tasks

1. Finalize benchmark summary with before/after latency and pass-rate deltas.
2. Add one command or script that runs the full validation pack.
3. Tighten docs around what Mantis is good at now.
4. Add launch checklist and failure-handling checklist.
5. Run final live/provider evaluation sweep.

### Verification

- `pytest -q`
- `python scripts/scenario_benchmark.py --include-server`
- `python scripts/stress_benchmark.py --loops 50`
- curated live/provider benchmark
- manual `/api/chat`, `/api/chat/stream`, and `/api/jobs` smoke tests

### Exit Gate

- full suite green
- synthetic and server benchmarks green
- live benchmark story is honest and documented
- latency limitations are narrowed enough that Mantis is strong on bounded work

## Can Mantis Execute This Plan?

Yes, mostly. The right way is not to give it "do four weeks of work" as one giant prompt. Break the plan into weekly and then daily bounded prompts.

Recommended pattern:

1. give Mantis one concrete task from the plan
2. run it in a temp repo or temp task directory when behavior is risky
3. require explicit verification commands
4. record the result in the benchmark/doc layer
5. move to the next bounded task

Bad prompt:

`Do the whole four week plan.`

Good prompt:

`Implement week 1 step 1 in this repo. Keep changes bounded to query_engine and tests. Run the relevant tests and summarize latency impact.`

## Real Test Harness Strategy

Use temp directories for real tasks before promoting patterns into the main repo.

Suggested workflow:

1. create a temp task repo or temp working directory
2. prompt Mantis with one bounded task
3. verify with pytest/check scripts
4. inspect produced diffs and traces
5. only then port the pattern into Mantis itself if the behavior is good

Examples of real tests:

- read-only file inspection in a temp repo
- single-file bugfix in a temp repo
- two-file feature implementation in a temp repo
- orchestrated multi-worker refactor in a temp git repo
- background job with approval/resume

## Suggested Temp Prompt File

Store prompts in temp markdown files and feed them to Mantis one at a time.

Example:

```md
# Task

Fix only `calc.py` so the existing pytest test passes.

# Constraints

- Do not modify tests
- Keep the answer short
- Run verification before finishing
```

Then run the matching verification immediately after the task completes.

## Immediate Next Step

Start with week 1, task 1:

- add per-task iteration budgets
- add tests for earlier exit on repeated tool loops
- run targeted tests
- run one real benchmark pass
