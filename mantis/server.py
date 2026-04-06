"""mantis/server.py — FastAPI web server for MantisAI dashboard."""
from __future__ import annotations

import json
import os
import asyncio
import threading
import subprocess
import queue
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from mantis.core.worktree_manager import collect_git_review, create_issue_worktree

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Start VIGIL watchdog on startup, stop on shutdown."""
    from mantis.core.job_store import JobStore
    from mantis.core.vigil import Vigil
    job_store = JobStore()
    vigil = Vigil(job_store)
    task = asyncio.create_task(vigil.run())
    try:
        yield
    finally:
        vigil.stop()
        task.cancel()


app = FastAPI(title="MantisAI", version="0.1.0", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3333", "http://127.0.0.1:3333"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_CONFIG_PATH = Path.home() / ".mantisai" / "config.json"
_DASHBOARD_PATH = Path(__file__).parent / "dashboard.html"

# Provider presets used by /api/models
_PROVIDERS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "intelligence_score": 8,
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "intelligence_score": 7,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "intelligence_score": 7,
    },
    "minimax": {
        "base_url": "https://api.minimax.chat/v1",
        "model": "abab6.5s-chat",
        "intelligence_score": 6,
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-3-5-sonnet",
        "intelligence_score": 10,
    },
}

_PROVIDER_KEY_FIELDS = {
    "openai_api_key": "OPENAI_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "dashscope_api_key": "DASHSCOPE_API_KEY",
    "qwen_api_key": "QWEN_API_KEY",
    "alibaba_api_key": "ALIBABA_CODING_API_KEY",
    "minimax_api_key": "MINIMAX_API_KEY",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_file_config() -> dict[str, Any]:
    """Load config from ~/.mantisai/config.json if it exists."""
    if _CONFIG_PATH.exists():
        try:
            return json.loads(_CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _resolve_config(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge: request overrides > file config > env vars."""
    file_cfg = _load_file_config()
    explicit_model = bool(os.environ.get("MANTIS_MODEL") or file_cfg.get("model"))
    if overrides and overrides.get("model") is not None:
        explicit_model = True

    env = {
        "model": os.environ.get("MANTIS_MODEL", "gpt-4o-mini"),
        "base_url": os.environ.get("MANTIS_BASE_URL", "https://api.openai.com/v1"),
        "api_key": os.environ.get("MANTIS_API_KEY", ""),
        "explicit_model": explicit_model,
    }
    for field, env_var in _PROVIDER_KEY_FIELDS.items():
        env[field] = os.environ.get(env_var, "")
    merged = {**env, **file_cfg}
    if overrides:
        merged = {**merged, **{k: v for k, v in overrides.items() if v is not None}}
    return merged


def _has_any_api_key(cfg: dict[str, Any]) -> bool:
    if cfg.get("api_key"):
        return True
    return any(cfg.get(field) for field in _PROVIDER_KEY_FIELDS)


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ConfigUpdate(BaseModel):
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    budget_usd: Optional[float] = None
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    dashscope_api_key: Optional[str] = None
    qwen_api_key: Optional[str] = None
    alibaba_api_key: Optional[str] = None
    minimax_api_key: Optional[str] = None


class ChatRequest(BaseModel):
    prompt: str
    system_prompt: Optional[str] = None
    session_id: Optional[str] = None


class BackgroundJobRequest(BaseModel):
    prompt: str
    system_prompt: Optional[str] = None
    session_id: Optional[str] = None
    issue_title: Optional[str] = None
    issue_number: Optional[int] = None
    repo_name: Optional[str] = None
    repo_dir: Optional[str] = None
    use_worktree: bool = False
    worktree_root_dir: Optional[str] = None
    create_draft_pr: bool = False


class ApprovalDecisionRequest(BaseModel):
    note: Optional[str] = None


_sessions: dict[str, "MantisApp"] = {}
_job_tasks: dict[str, Any] = {}


async def _run_coro_in_thread(awaitable_factory) -> Any:
    """Run an async workload on a worker thread with its own event loop."""
    return await asyncio.to_thread(lambda: asyncio.run(awaitable_factory()))


async def _stream_asyncgen_in_thread(asyncgen_factory):
    """Bridge an async generator from a worker thread into the FastAPI event loop."""
    sentinel = object()
    buffer: queue.Queue[Any] = queue.Queue()

    async def _consume() -> None:
        try:
            async for item in asyncgen_factory():
                buffer.put(item)
        except Exception as exc:  # pragma: no cover - surfaced to caller below
            buffer.put(exc)
        finally:
            buffer.put(sentinel)

    thread = threading.Thread(
        target=lambda: asyncio.run(_consume()),
        daemon=True,
        name="mantis-stream-worker",
    )
    thread.start()

    while True:
        item = await asyncio.to_thread(buffer.get)
        if item is sentinel:
            break
        if isinstance(item, Exception):
            raise item
        yield item


def _log_event(
    session_id: str,
    event_type: str,
    message: str,
    job_id: str | None = None,
    approval_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    from mantis.core.activity_store import ActivityStore

    ActivityStore().create(
        session_id=session_id,
        event_type=event_type,
        message=message,
        job_id=job_id,
        approval_id=approval_id,
        metadata=metadata,
    )


def _extract_execution_summary(stats: dict[str, Any] | None) -> dict[str, Any]:
    stats = stats or {}
    execution = stats.get("execution") or {}
    return {
        "execution_mode": execution.get("execution_mode"),
        "tasks": execution.get("tasks") or [],
        "verification": execution.get("verifier"),
        "context": execution.get("context"),
        "workers": execution.get("workers"),
        "worker_summary": execution.get("worker_summary"),
        "pr_review": execution.get("pr_review"),
        "review_bundle": execution.get("review_bundle"),
        "worktree": execution.get("worktree"),
        "draft_pr": execution.get("draft_pr"),
    }


def _build_review_bundle_payload(
    *,
    prompt: str,
    response: str | None,
    stats: dict[str, Any] | None,
    git_review: dict[str, Any] | None,
    issue_title: str | None = None,
    issue_number: int | None = None,
) -> dict[str, Any]:
    from mantis.cli import build_pr_review_bundle

    stats = stats or {}
    execution = stats.get("execution") or {}
    verification = execution.get("verifier") or {}
    worker_summary = execution.get("worker_summary") or {}
    changed_files = list((git_review or {}).get("changed_files") or worker_summary.get("changed_files") or [])
    title = issue_title or prompt.strip().splitlines()[0][:120] or "Mantis change bundle"
    body = build_pr_review_bundle(
        title,
        response or "",
        stats=stats,
        issue_number=issue_number,
        git_review=git_review,
    )
    return {
        "title": f"[Issue #{issue_number}] {title}" if issue_number is not None else title,
        "body": body,
        "changed_files": changed_files,
        "verdict": verification.get("verdict"),
        "reason": verification.get("reason"),
        "branch": (git_review or {}).get("branch"),
        "worker_count": worker_summary.get("worker_count"),
    }


def _serialize_job(job: Any) -> dict[str, Any]:
    payload = job.to_dict()
    metadata = payload.get("metadata") or {}
    execution = metadata.get("execution") or {}
    approval = None
    if metadata.get("approval_id") or metadata.get("tool_name"):
        approval = {
            "approval_id": metadata.get("approval_id"),
            "tool_name": metadata.get("tool_name"),
            "risk_level": metadata.get("risk_level"),
            "status": "awaiting_approval" if payload.get("status") == "awaiting_approval" else None,
        }
    resume = None
    if metadata.get("resumed_from_approval_id"):
        resume = {
            "approval_id": metadata.get("resumed_from_approval_id"),
            "tool_name": metadata.get("resumed_tool_name"),
            "note": metadata.get("resume_note"),
        }
    payload["tasks"] = execution.get("tasks") or (metadata.get("plan") or {}).get("tasks") or []
    payload["verification"] = execution.get("verifier")
    payload["execution_mode"] = execution.get("execution_mode")
    payload["context"] = execution.get("context")
    payload["workers"] = execution.get("workers") or metadata.get("workers")
    payload["worker_summary"] = execution.get("worker_summary") or metadata.get("worker_summary")
    payload["pr_review"] = execution.get("pr_review") or metadata.get("pr_review")
    payload["review_bundle"] = execution.get("review_bundle") or metadata.get("review_bundle")
    payload["worktree"] = execution.get("worktree") or metadata.get("worktree")
    payload["draft_pr"] = execution.get("draft_pr") or metadata.get("draft_pr")
    payload["approval"] = approval
    payload["resume"] = resume
    return payload


def _create_draft_pr_with_gh(
    title: str,
    body: str,
    branch: str,
    repo_name: str | None = None,
) -> str:
    cmd = ["gh", "pr", "create", "--draft", "--title", title, "--body", body, "--head", branch]
    if repo_name:
        cmd.extend(["--repo", repo_name])
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as e:
        raise RuntimeError("gh CLI is not installed or not on PATH") from e
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        raise RuntimeError(f"gh pr create failed: {stderr or e}") from e
    return (proc.stdout or "").strip()


def _get_session_app(session_id: str, cfg: dict[str, Any]) -> "MantisApp":
    from mantis.app import MantisApp

    mantis_cfg = {
        "model": cfg["model"],
        "base_url": cfg["base_url"],
        "api_key": cfg["api_key"],
        "budget_usd": cfg.get("budget_usd"),
        "explicit_model": cfg.get("explicit_model", False),
        "openai_api_key": cfg.get("openai_api_key"),
        "anthropic_api_key": cfg.get("anthropic_api_key"),
        "deepseek_api_key": cfg.get("deepseek_api_key"),
        "dashscope_api_key": cfg.get("dashscope_api_key"),
        "qwen_api_key": cfg.get("qwen_api_key"),
        "alibaba_api_key": cfg.get("alibaba_api_key"),
        "minimax_api_key": cfg.get("minimax_api_key"),
    }
    current = _sessions.get(session_id)
    if current is None or current.config != mantis_cfg:
        _sessions[session_id] = MantisApp(mantis_cfg, session_id=session_id)
    return _sessions[session_id]


def _build_job_app(session_id: str, cfg: dict[str, Any]) -> "MantisApp":
    from mantis.app import MantisApp

    mantis_cfg = {
        "model": cfg["model"],
        "base_url": cfg["base_url"],
        "api_key": cfg["api_key"],
        "budget_usd": cfg.get("budget_usd"),
        "explicit_model": cfg.get("explicit_model", False),
        "openai_api_key": cfg.get("openai_api_key"),
        "anthropic_api_key": cfg.get("anthropic_api_key"),
        "deepseek_api_key": cfg.get("deepseek_api_key"),
        "dashscope_api_key": cfg.get("dashscope_api_key"),
        "qwen_api_key": cfg.get("qwen_api_key"),
        "alibaba_api_key": cfg.get("alibaba_api_key"),
        "minimax_api_key": cfg.get("minimax_api_key"),
    }
    return MantisApp(mantis_cfg, session_id=session_id)


def _start_background_thread(job_id: str, runner) -> None:
    def _thread_target() -> None:
        try:
            asyncio.run(runner())
        finally:
            _job_tasks.pop(job_id, None)

    thread = threading.Thread(target=_thread_target, daemon=True, name=f"mantis-job-{job_id[:8]}")
    _job_tasks[job_id] = thread
    thread.start()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def serve_dashboard():
    if not _DASHBOARD_PATH.exists():
        raise HTTPException(status_code=404, detail="dashboard.html not found")
    return FileResponse(str(_DASHBOARD_PATH), media_type="text/html")


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/config")
async def get_config():
    cfg = _resolve_config()
    # List tools via a lightweight MantisApp instance (no API call needed)
    try:
        from mantis.app import MantisApp
        tmp = MantisApp({"api_key": cfg.get("api_key", ""), "model": cfg.get("model", "")})
        tools = list(tmp.list_tools().keys())
    except Exception:
        tools = []

    return {
        "model": cfg.get("model", ""),
        "base_url": cfg.get("base_url", ""),
        "budget_usd": cfg.get("budget_usd"),
        "api_key_masked": _mask_key(cfg.get("api_key", "")),
        "provider_keys_masked": {
            field: _mask_key(cfg.get(field, "")) for field in _PROVIDER_KEY_FIELDS
        },
        "tools": tools,
    }


@app.post("/api/config")
async def save_config(body: ConfigUpdate):
    existing = _load_file_config()
    update: dict[str, Any] = {}
    if body.model is not None:
        update["model"] = body.model
    if body.base_url is not None:
        update["base_url"] = body.base_url
    if body.api_key is not None:
        update["api_key"] = body.api_key
    if body.budget_usd is not None:
        update["budget_usd"] = body.budget_usd
    for field in _PROVIDER_KEY_FIELDS:
        value = getattr(body, field)
        if value is not None:
            update[field] = value

    merged = {**existing, **update}
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(merged, indent=2))
    return {"ok": True, "saved": list(update.keys())}


@app.post("/api/chat")
async def chat(body: ChatRequest):
    cfg = _resolve_config()
    if not _has_any_api_key(cfg):
        raise HTTPException(
            status_code=400,
            detail="No API key configured. Set it in the sidebar or via MANTIS_API_KEY.",
        )

    try:
        from mantis.core.planner import build_execution_plan

        session_id = body.session_id or "default"
        app_instance = _get_session_app(session_id, cfg)

        # Determine task metadata before running
        plan = build_execution_plan(body.prompt)
        task_type = plan.tasks[0].task_type if plan.tasks else "unknown"
        subtasks_count = len(plan.tasks)

        response = await _run_coro_in_thread(
            lambda: app_instance._run_chat(body.prompt)
        )

        return {
            "response": response,
            "task_type": task_type,
            "subtasks_count": subtasks_count,
            "model": app_instance.last_stats.get("model", cfg["model"]),
            "stats": app_instance.last_stats,
            **_extract_execution_summary(app_instance.last_stats),
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/jobs")
async def create_background_job(body: BackgroundJobRequest):
    cfg = _resolve_config()
    if not _has_any_api_key(cfg):
        raise HTTPException(
            status_code=400,
            detail="No API key configured. Set it in the sidebar or via MANTIS_API_KEY.",
        )

    try:
        from mantis.core.job_store import JobStore
        from mantis.core.planner import build_execution_plan

        session_id = body.session_id or "default"
        plan = build_execution_plan(body.prompt)
        task_type = plan.tasks[0].task_type if plan.tasks else "unknown"
        subtasks_count = len(plan.tasks)
        worktree_meta = None
        project_dir = None
        repo_dir = body.repo_dir or os.getcwd()
        if body.use_worktree:
            worktree_title = body.issue_title or body.prompt[:80]
            worktree_meta = create_issue_worktree(
                repo_dir=repo_dir,
                title=worktree_title,
                issue_number=body.issue_number,
                root_dir=body.worktree_root_dir,
            )
            project_dir = worktree_meta["worktree_dir"]

        store = JobStore()
        job = store.create(
            prompt=body.prompt,
            session_id=session_id,
            model=cfg["model"],
            task_type=task_type,
            subtasks_count=subtasks_count,
            plan=plan.to_dict(),
            issue_title=body.issue_title,
            issue_number=body.issue_number,
            repo_name=body.repo_name,
            worktree=worktree_meta,
        )
        _log_event(
            session_id=session_id,
            event_type="job_queued",
            message=f"Queued background job: {body.prompt[:80]}",
            job_id=job.id,
            metadata={"task_type": task_type},
        )

        async def _runner() -> None:
            app_instance = _build_job_app(session_id, cfg)
            if project_dir:
                from mantis.app import MantisApp
                app_instance = MantisApp(cfg, project_dir=project_dir, session_id=session_id)
            store.update(job.id, status="running")
            _log_event(
                session_id=session_id,
                event_type="job_running",
                message=f"Running background job {job.id[:8]}",
                job_id=job.id,
            )
            try:
                response = await app_instance._run_chat(body.prompt, job_id=job.id)
                execution = app_instance.last_stats.setdefault("execution", {})
                git_review = None
                if worktree_meta:
                    try:
                        git_review = collect_git_review(worktree_meta["worktree_dir"])
                    except RuntimeError:
                        git_review = {
                            "branch": worktree_meta["branch"],
                            "path": worktree_meta["worktree_dir"],
                            "changed_files": [],
                            "diff": "",
                        }
                    execution["worktree"] = {
                        "branch": git_review.get("branch"),
                        "path": git_review.get("path"),
                    }
                review_bundle = None
                if worktree_meta or execution.get("workers"):
                    review_bundle = _build_review_bundle_payload(
                        prompt=body.prompt,
                        response=response,
                        stats=app_instance.last_stats,
                        git_review=git_review,
                        issue_title=body.issue_title,
                        issue_number=body.issue_number,
                    )
                    execution["review_bundle"] = review_bundle
                if body.issue_title:
                    verifier = execution.get("verifier") or {}
                    changed_files = list((git_review or {}).get("changed_files") or [])
                    if not changed_files:
                        for task in execution.get("tasks") or []:
                            for target in task.get("file_targets") or []:
                                if target not in changed_files:
                                    changed_files.append(target)
                    execution["pr_review"] = {
                        "title": f"[Issue #{body.issue_number}] {body.issue_title}" if body.issue_number is not None else body.issue_title,
                        "changed_files": changed_files,
                        "verdict": verifier.get("verdict"),
                        "reason": verifier.get("reason"),
                        "diff_preview": (git_review or {}).get("diff"),
                        "body": (review_bundle or {}).get("body"),
                    }
                    if body.create_draft_pr:
                        branch = (git_review or {}).get("branch")
                        if branch:
                            pr_body = execution["pr_review"].get("body") or ""
                            try:
                                pr_url = _create_draft_pr_with_gh(
                                    execution["pr_review"]["title"],
                                    pr_body,
                                    branch,
                                    body.repo_name,
                                )
                                execution["draft_pr"] = {
                                    "status": "created",
                                    "url": pr_url,
                                    "branch": branch,
                                }
                            except RuntimeError as exc:
                                execution["draft_pr"] = {
                                    "status": "error",
                                    "error": str(exc),
                                    "branch": branch,
                                }
                store.update(
                    job.id,
                    status="done",
                    response=response,
                    model=app_instance.last_stats.get("model", cfg["model"]),
                    metadata={**job.metadata, **app_instance.last_stats},
                )
                _log_event(
                    session_id=session_id,
                    event_type="job_done",
                    message=f"Completed background job {job.id[:8]}",
                    job_id=job.id,
                    metadata=app_instance.last_stats,
                )
            except Exception as exc:
                from mantis.core.permissions import PermissionRequiredError

                if isinstance(exc, PermissionRequiredError):
                    store.update(
                        job.id,
                        status="awaiting_approval",
                        error=f"Approval required for {exc.tool_name}",
                        metadata={
                            **job.metadata,
                            "approval_id": exc.approval_id,
                            "tool_name": exc.tool_name,
                            "risk_level": exc.risk_level,
                            "resumed_from_approval_id": None,
                            "resumed_tool_name": None,
                            "resume_note": None,
                        },
                    )
                    _log_event(
                        session_id=session_id,
                        event_type="approval_required",
                        message=f"Approval required for {exc.tool_name}",
                        job_id=job.id,
                        approval_id=exc.approval_id,
                        metadata={"tool_name": exc.tool_name, "risk_level": exc.risk_level},
                    )
                else:
                    store.update(job.id, status="failed", error=str(exc))
                    _log_event(
                        session_id=session_id,
                        event_type="job_failed",
                        message=f"Background job {job.id[:8]} failed",
                        job_id=job.id,
                        metadata={"error": str(exc)},
                    )
            finally:
                pass

        _start_background_thread(job.id, _runner)
        return {"job_id": job.id, "status": job.status}

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/jobs")
async def list_background_jobs(limit: int = 20):
    from mantis.core.job_store import JobStore

    store = JobStore()
    jobs = [_serialize_job(job) for job in store.list(limit=limit)]
    return {"jobs": jobs}


@app.get("/api/approvals")
async def list_approvals(limit: int = 20, status: Optional[str] = None):
    from mantis.core.approval_store import ApprovalStore

    store = ApprovalStore()
    approvals = [approval.to_dict() for approval in store.list(limit=limit, status=status)]
    return {"approvals": approvals}


@app.get("/api/activity")
async def list_activity(session_id: Optional[str] = None, limit: int = 40):
    from mantis.core.activity_store import ActivityStore

    events = [event.to_dict() for event in ActivityStore().list(session_id=session_id, limit=limit)]
    return {"events": events}


@app.get("/api/traces")
async def list_traces(
    session_id: Optional[str] = None,
    limit: int = 40,
    execution_mode: Optional[str] = None,
    verifier_verdict: Optional[str] = None,
):
    from mantis.core.trace_store import TraceStore

    traces = [
        trace.to_dict()
        for trace in TraceStore().list(
            session_id=session_id,
            limit=limit,
            execution_mode=execution_mode,
            verifier_verdict=verifier_verdict,
        )
    ]
    return {"traces": traces}


@app.post("/api/approvals/{approval_id}/approve")
async def approve_request(approval_id: str, body: ApprovalDecisionRequest):
    from mantis.core.approval_store import ApprovalStore
    from mantis.core.job_store import JobStore

    approvals = ApprovalStore()
    approval = approvals.load(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")

    approval = approvals.update(approval_id, status="approved", decision_note=body.note)
    resumed_job_id = approval.job_id if approval is not None else None
    if approval is not None and approval.job_id:
        jobs = JobStore()
        job = jobs.load(approval.job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found for approval")

        _log_event(
            session_id=approval.session_id,
            event_type="approval_approved",
            message=f"Approved {approval.tool_name}",
            job_id=job.id,
            approval_id=approval_id,
            metadata={"note": body.note},
        )

        cfg = _resolve_config()

        async def _resume_runner() -> None:
            app_instance = _build_job_app(job.session_id, cfg)
            jobs.update(
                job.id,
                status="running",
                error=None,
                metadata={
                    **job.metadata,
                    "approval_id": approval_id,
                    "tool_name": approval.tool_name,
                    "risk_level": approval.risk_level,
                    "resumed_from_approval_id": approval_id,
                    "resumed_tool_name": approval.tool_name,
                    "resume_note": body.note,
                },
            )
            _log_event(
                session_id=approval.session_id,
                event_type="job_resumed",
                message=f"Resumed job {job.id[:8]} from approval",
                job_id=job.id,
                approval_id=approval_id,
            )
            try:
                if approval.metadata.get("kind") == "model_escalation":
                    response = await app_instance._run_chat(
                        approval.metadata.get("prompt", job.prompt),
                        job_id=job.id,
                    )
                else:
                    response = await app_instance._resume_approval(approval_id, job_id=job.id)
                jobs.update(
                    job.id,
                    status="done",
                    response=response,
                    model=app_instance.last_stats.get("model", job.model),
                    metadata={**job.metadata, **app_instance.last_stats},
                    error=None,
                )
                _log_event(
                    session_id=approval.session_id,
                    event_type="job_done",
                    message=f"Completed background job {job.id[:8]}",
                    job_id=job.id,
                    approval_id=approval_id,
                    metadata=app_instance.last_stats,
                )
            except Exception as exc:
                from mantis.core.permissions import PermissionRequiredError

                if isinstance(exc, PermissionRequiredError):
                    jobs.update(
                        job.id,
                        status="awaiting_approval",
                        error=f"Approval required for {exc.tool_name}",
                        metadata={
                            **job.metadata,
                            "approval_id": exc.approval_id,
                            "tool_name": exc.tool_name,
                            "risk_level": exc.risk_level,
                            "resumed_from_approval_id": approval_id,
                            "resumed_tool_name": approval.tool_name,
                            "resume_note": body.note,
                        },
                    )
                    _log_event(
                        session_id=approval.session_id,
                        event_type="approval_required",
                        message=f"Approval required for {exc.tool_name}",
                        job_id=job.id,
                        approval_id=exc.approval_id,
                        metadata={"tool_name": exc.tool_name, "risk_level": exc.risk_level},
                    )
                else:
                    jobs.update(job.id, status="failed", error=str(exc))
                    _log_event(
                        session_id=approval.session_id,
                        event_type="job_failed",
                        message=f"Background job {job.id[:8]} failed",
                        job_id=job.id,
                        approval_id=approval_id,
                        metadata={"error": str(exc)},
                    )
            finally:
                pass

        _start_background_thread(job.id, _resume_runner)

    return {"ok": True, "approval_id": approval_id, "resumed_job_id": resumed_job_id}


@app.post("/api/approvals/{approval_id}/deny")
async def deny_request(approval_id: str, body: ApprovalDecisionRequest):
    from mantis.core.approval_store import ApprovalStore
    from mantis.core.job_store import JobStore

    approvals = ApprovalStore()
    approval = approvals.load(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")

    approvals.update(approval_id, status="denied", decision_note=body.note)
    if approval.job_id:
        jobs = JobStore()
        jobs.update(
            approval.job_id,
            status="failed",
            error=f"Approval denied for {approval.tool_name}",
            metadata={**approval.metadata, "approval_id": approval.id},
        )
        _log_event(
            session_id=approval.session_id,
            event_type="approval_denied",
            message=f"Denied {approval.tool_name}",
            job_id=approval.job_id,
            approval_id=approval.id,
            metadata={"note": body.note},
        )

    return {"ok": True, "approval_id": approval_id}


@app.get("/api/jobs/{job_id}")
async def get_background_job(job_id: str):
    from mantis.core.job_store import JobStore

    store = JobStore()
    job = store.load(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _serialize_job(job)


@app.post("/api/jobs/{job_id}/resume")
async def resume_background_job(job_id: str):
    from mantis.core.job_store import JobStore

    store = JobStore()
    job = store.load(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return await create_background_job(
        BackgroundJobRequest(
            prompt=job.prompt,
            session_id=job.session_id,
        )
    )


@app.post("/api/jobs/{job_id}/rerun-failed-workers")
async def rerun_failed_workers(job_id: str):
    from mantis.core.job_store import JobStore

    store = JobStore()
    job = store.load(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    metadata = job.metadata or {}
    execution = metadata.get("execution") or metadata
    workers = execution.get("workers") or []
    failed_workers = [
        worker
        for worker in workers
        if str(worker.get("status", "")).lower() not in {"completed", "done", "pass"}
    ]
    if not failed_workers:
        raise HTTPException(status_code=400, detail="No failed workers available to rerun")

    prompts = []
    repo_dir = None
    for worker in failed_workers:
        resume_metadata = worker.get("resume_metadata") or {}
        prompt = resume_metadata.get("prompt")
        if prompt:
            prompts.append(prompt)
        repo_dir = repo_dir or resume_metadata.get("project_dir")
        worktree_dir = resume_metadata.get("worktree_dir")
        if repo_dir is None and worktree_dir:
            repo_dir = str(Path(worktree_dir).parent.parent)

    if not prompts:
        raise HTTPException(status_code=400, detail="Failed workers do not have rerun metadata")

    rerun_prompt = "\n\nand then\n\n".join(prompts)
    return await create_background_job(
        BackgroundJobRequest(
            prompt=rerun_prompt,
            session_id=job.session_id,
            repo_dir=repo_dir,
        )
    )


@app.get("/api/sessions/{session_id}")
async def get_session_checkpoint(session_id: str):
    from mantis.core.session_store import SessionStore

    store = SessionStore()
    session = store.load(session_id)
    return session.to_dict()


@app.post("/api/chat/stream")
async def chat_stream(body: ChatRequest):
    cfg = _resolve_config()
    if not _has_any_api_key(cfg):
        raise HTTPException(
            status_code=400,
            detail="No API key configured. Set it in the sidebar or via MANTIS_API_KEY.",
        )

    try:
        from mantis.core.system_prompt import build_system_prompt

        session_id = body.session_id or "default"
        app_instance = _get_session_app(session_id, cfg)
        fast_path = app_instance._try_local_fast_path(body.prompt)

        system_prompt = body.system_prompt or build_system_prompt()

        async def generator():
            if fast_path is not None:
                response, routing, execution = fast_path
                file_targets = execution["tasks"][0].get("file_targets", []) if execution.get("tasks") else []
                verifier_reason = (execution.get("verifier") or {}).get("reason") or "Local fast path completed."
                task_type = routing.get("task_type", "review")
                yield f'data: {json.dumps({"type": "status", "text": "Planning..."})}\n\n'
                yield f'data: {json.dumps({"type": "plan", "tasks": [{"title": execution["tasks"][0]["title"], "task_type": task_type, "status": "pending", "file_targets": file_targets}], "execution_mode": "local_fast_path"})}\n\n'
                for chunk in app_instance.query_engine._stream_response_chunks(response):
                    yield f'data: {json.dumps({"type": "token", "content": chunk})}\n\n'
                yield f'data: {json.dumps({"type": "done", "task_type": task_type, "subtasks": 1, "total_cost": 0.0, "remaining_budget_usd": cfg.get("budget_usd"), "execution_mode": "local_fast_path", "verifier_reason": verifier_reason, "verifier_verdict": "pass"})}\n\n'
                return

            async for chunk in _stream_asyncgen_in_thread(
                lambda: app_instance.query_engine.run_streaming(
                    body.prompt,
                    system_prompt=system_prompt,
                )
            ):
                yield chunk

        return StreamingResponse(
            content=generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/models")
async def list_models():
    cfg = _resolve_config()
    try:
        from mantis.app import MantisApp
        tmp = MantisApp({
            "model": cfg.get("model", ""),
            "base_url": cfg.get("base_url", ""),
            "api_key": cfg.get("api_key", ""),
        })
        models = tmp.list_models()
    except Exception:
        models = {}

    # Merge with provider presets for any missing entries
    for provider, preset in _PROVIDERS.items():
        if preset["model"] not in models:
            models[preset["model"]] = {
                "provider": provider,
                "intelligence_score": preset["intelligence_score"],
                "cost_per_1k_tokens": 0.0,
                "context_window": 128000,
            }

    return models


# ---------------------------------------------------------------------------
# Entry point for `python -m mantis.server`
# ---------------------------------------------------------------------------

def run(host: str = "localhost", port: int = 3333) -> None:
    import uvicorn
    uvicorn.run("mantis.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="MantisAI web server")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=3333)
    args = parser.parse_args()
    run(host=args.host, port=args.port)
