# Founder Build Brief

## What We Are Building

Mantis should become the fastest credible open coding agent for bounded engineering work: read a repo, make a change, prove it, and show its work.

This is not an "agent OS" pitch. The wedge is practical execution:

- cheap-by-default routing
- visible approvals
- background jobs
- isolated workers
- verifier-backed completion
- benchmarked real tasks

## What Must Be True In 4 Weeks

- bounded read/edit/refactor tasks feel reliable
- multi-worker runs are isolated and reviewable
- health and job control stay responsive under load
- the dashboard shows what happened without guesswork
- synthetic and live benchmarks tell an honest story

## Product Standard

Mantis should be able to:

1. take one concrete coding task
2. choose the cheapest safe execution path
3. isolate risky multi-file work
4. verify the result
5. expose the diff, worker ownership, and decision trail

## Execution Priorities

### 1. Latency

Stop wasting time after the system already has enough information to answer or finish.

### 2. Worker Isolation

Every serious multi-worker task should be inspectable, branchable, and recoverable.

### 3. Observability

If Mantis fails, we should know which worker, which file, which verifier step, and which approval caused it.

### 4. Live Proof

The system needs real task evidence, not only passing unit tests.

## Non-Goals

- broad open-ended "autonomous software company" claims
- adding more agent roles without stronger control loops
- polishing marketing before runtime behavior is strong

## 4-Week Outcome

At the end of this cycle, Mantis should be strong on bounded coding work and honest about where model-backed latency still limits it.
