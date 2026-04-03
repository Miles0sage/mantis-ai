# Show HN Draft

**Title:** Show HN: MantisAI – cheap-by-default async coding agent with approvals and cost control

---

## Post Body

We built MantisAI because most open-source coding agents still optimize for terminal parity or raw model quality, not for visible control. We wanted something cheap by default, browser-first, and safe to leave running in the background.

Mantis runs coding jobs with hard budget ceilings, explicit approvals for risky actions, checkpoint/resume, and a dashboard that shows task tree, activity feed, and verifier state. Simple work can stay on cheap models like DeepSeek or Qwen. Stronger models are reserved for harder tasks instead of being the default for everything.

The important part is that Mantis shows its work. File edits can pause for approval with a preview. Background jobs resume from the same checkpoint after approval. Generated tests and checker files are used as artifact gates before a task is treated as complete. The UI is not just a chat box; it is the control surface.

Current benchmark status is good: strict codegen, multi-step generation, surgical edit, and background approval/resume all pass in live runs. The repo currently has 134 passing tests.

What we think is interesting is not “open-source Claude Code clone.” The wedge is: async coding agent with visible approvals, verifier-backed completion, and cost control that makes cheap models practical.

Repo: https://github.com/Miles0sage/mantis-ai

```bash
pip install mantisai
export MANTIS_API_KEY=your-key
export MANTIS_MODEL=gpt-4o-mini
mantisai chat
```

Happy to answer questions about the architecture or the model routing approach.

---

## Potential Follow-up Comments to Prepare For

**"How does this compare to Aider?"**
Aider is stronger and more mature for pure editing workflows. Mantis is trying to win on async jobs, approvals, checkpoint resume, browser control, and visible cost-aware execution.

**"Why not just use vendor SDKs?"**
Because the point here is provider portability plus explicit control. The product only makes sense if cheap models are first-class, not a second-tier fallback.

**"What actually makes this safer?"**
Approvals, diff previews, hard budget ceilings, and verifier-backed completion. The model does not just say “done” and hope you trust it.

**"$0.001 per task — is that realistic?"**
For simple work, yes. Harder jobs cost more, but the spend is visible and capped. The point is not magical cheapness on every task; it is that the system is designed around cost instead of hiding it.
