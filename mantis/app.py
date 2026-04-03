import asyncio
import hashlib
import os
from typing import Any

from mantis.agents.orchestrator import CoordinatorOrchestrator
from mantis.core.approval_store import ApprovalStore
from mantis.core.context_manager import ContextManager
from mantis.core.hooks import HookManager
from mantis.core.model_adapter import ModelAdapter
from mantis.core.permissions import PermissionManager
from mantis.core.planner import build_execution_plan
from mantis.core.query_engine import QueryEngine
from mantis.core.router import ModelProfile, ModelRouter
from mantis.core.session_store import SessionStore
from mantis.core.system_prompt import build_system_prompt
from mantis.core.tool_registry import ToolRegistry
from mantis.tools.builtins import register_builtins


DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
PROVIDER_KEY_ENV_MAP = {
    "openai-compatible": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "alibaba": "DASHSCOPE_API_KEY",
    "minimax": "MINIMAX_API_KEY",
}


class MantisApp:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
        project_dir: str | None = None,
        session_id: str = "default",
    ) -> None:
        self.config = config or {}
        self.project_dir = project_dir or os.getcwd()
        self.session_id = session_id
        self.last_stats: dict[str, Any] = {}
        self.session_store = SessionStore()
        self.session_record = self.session_store.load(session_id)
        self.approval_store = ApprovalStore()
        self.router = self._build_router()
        self.tool_registry = ToolRegistry()
        register_builtins(self.tool_registry)
        if "explicit_model" in self.config:
            self._explicit_model_requested = bool(self.config.get("explicit_model"))
        else:
            self._explicit_model_requested = bool(
                self.config.get("model") or os.environ.get("MANTIS_MODEL")
            )

        profile = self._select_model_profile()
        self.model_adapter = self._create_model_adapter(profile)
        self.context_manager = ContextManager(max_tokens=128000)
        self.hook_manager = HookManager()
        # CLI / non-interactive: yolo lets run_bash execute without approval prompts.
        # Server / background jobs: auto mode uses the approval store flow.
        import sys as _sys
        _perm_mode = self.config.get("permission_mode", "yolo" if not _sys.stdin.isatty() else "auto")
        self.permission_manager = PermissionManager(
            mode=_perm_mode,
            approval_store=self.approval_store,
        )
        self.query_engine = QueryEngine(
            model_adapter=self.model_adapter,
            tool_registry=self.tool_registry,
            max_iterations=self.config.get("max_iterations", 25),
            context_manager=self.context_manager,
            hook_manager=self.hook_manager,
            permission_manager=self.permission_manager,
            router=self.router,
        )

    def _build_router(self) -> ModelRouter:
        router = ModelRouter()
        base_url = self.config.get("base_url") or os.environ.get(
            "MANTIS_BASE_URL",
            DEFAULT_BASE_URL,
        )
        model_name = self.config.get("model") or os.environ.get("MANTIS_MODEL", DEFAULT_MODEL)
        base_provider = self._provider_from_base_url(base_url)

        router.add_model(
            ModelProfile(
                name=model_name,
                base_url=base_url,
                api_key=self._resolve_api_key(base_provider),
                intelligence_score=8,
                cost_per_1k_input=0.0005,
                cost_per_1k_output=0.0015,
                context_window=128000,
                supports_tools=True,
                supports_streaming=True,
            )
        )

        # Reference profiles for listing and future routing.
        router.add_model(
            ModelProfile(
                name="deepseek-chat",
                base_url="https://api.deepseek.com/v1",
                api_key=self._resolve_api_key("deepseek"),
                intelligence_score=7,
                cost_per_1k_input=0.00027,
                cost_per_1k_output=0.0011,
                context_window=64000,
                supports_tools=True,
                supports_streaming=True,
            )
        )
        router.add_model(
            ModelProfile(
                name="qwen-plus",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                api_key=self._resolve_api_key("alibaba"),
                intelligence_score=7,
                cost_per_1k_input=0.0004,
                cost_per_1k_output=0.0012,
                context_window=128000,
                supports_tools=True,
                supports_streaming=True,
            )
        )
        router.add_model(
            ModelProfile(
                name="claude-3-5-sonnet",
                base_url="https://api.anthropic.com/v1",
                api_key=self._resolve_api_key("anthropic"),
                intelligence_score=10,
                cost_per_1k_input=0.003,
                cost_per_1k_output=0.015,
                context_window=200000,
                supports_tools=True,
                supports_streaming=True,
            )
        )
        router.add_model(
            ModelProfile(
                name="abab6.5s-chat",
                base_url="https://api.minimax.chat/v1",
                api_key=self._resolve_api_key("minimax"),
                intelligence_score=6,
                cost_per_1k_input=0.0008,
                cost_per_1k_output=0.0016,
                context_window=128000,
                supports_tools=True,
                supports_streaming=True,
            )
        )

        return router

    def _provider_from_base_url(self, base_url: str) -> str:
        host = base_url.lower()
        if "deepseek" in host:
            return "deepseek"
        if "anthropic" in host:
            return "anthropic"
        if "minimax" in host:
            return "minimax"
        if "dashscope" in host or "alibaba" in host:
            return "alibaba"
        if "ollama" in host:
            return "ollama"
        return "openai-compatible"

    def _resolve_api_key(self, provider: str) -> str:
        provider_config_keys = {
            "openai-compatible": ["openai_api_key", "api_key"],
            "anthropic": ["anthropic_api_key", "api_key"],
            "deepseek": ["deepseek_api_key", "api_key"],
            "alibaba": ["dashscope_api_key", "qwen_api_key", "alibaba_api_key", "api_key"],
            "minimax": ["minimax_api_key", "api_key"],
        }
        for key in provider_config_keys.get(provider, ["api_key"]):
            value = self.config.get(key)
            if value:
                return value
        env_key = PROVIDER_KEY_ENV_MAP.get(provider)
        if env_key:
            return os.environ.get(env_key, "") or os.environ.get("MANTIS_API_KEY", "")
        return os.environ.get("MANTIS_API_KEY", "")

    def _select_model_profile(self) -> ModelProfile:
        configured = self.config.get("model") or os.environ.get("MANTIS_MODEL")
        if configured:
            for model in self.router.list_models():
                if model.name == configured:
                    return model
        # Prefer the first model added (the user-configured one) over routing
        models = self.router.list_models()
        if models:
            return models[0]
        complexity = self.config.get("complexity", "medium")
        return self.router.route(complexity)

    def _create_model_adapter(self, profile: ModelProfile) -> ModelAdapter:
        return ModelAdapter(
            base_url=profile.base_url,
            api_key=profile.api_key,
            model=profile.name,
            max_tokens=self.config.get("max_tokens", 4096),
            cost_per_1k_input=profile.cost_per_1k_input,
            cost_per_1k_output=profile.cost_per_1k_output,
            max_budget_usd=self.config.get("budget_usd"),
        )

    def _resolve_model_for_prompt(self, prompt: str) -> tuple[ModelProfile, dict[str, Any]]:
        plan = build_execution_plan(prompt, cwd=self.project_dir)
        unique_files = {
            file_target
            for task in plan.tasks
            for file_target in task.file_targets
        }

        if self._explicit_model_requested:
            return self._select_model_profile(), {
                "strategy": "explicit_override",
                "task_type": plan.task_type,
                "complexity": plan.complexity,
                "file_count": len(unique_files),
                "task_count": len(plan.tasks),
                "needs_escalation": plan.needs_escalation,
            }

        return self.router.route_for_plan(
            task_type=plan.task_type,
            complexity=plan.complexity,
            file_count=len(unique_files),
            task_count=len(plan.tasks),
            needs_escalation=plan.needs_escalation,
        ), {
            "strategy": "auto_plan_router",
            "task_type": plan.task_type,
            "complexity": plan.complexity,
            "file_count": len(unique_files),
            "task_count": len(plan.tasks),
            "needs_escalation": plan.needs_escalation,
        }

    def _load_project_instructions(self) -> str | None:
        mantis_md = os.path.join(self.project_dir, "MANTIS.md")
        if os.path.isfile(mantis_md):
            try:
                with open(mantis_md, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                return None
        return None

    def _require_api_key(self, profile: ModelProfile | None = None) -> None:
        effective_key = profile.api_key if profile is not None else self.model_adapter.api_key
        if effective_key:
            return
        raise ValueError(
            "No API key configured. Set MANTIS_API_KEY or pass --api-key."
        )

    def _maybe_require_model_escalation_approval(
        self,
        prompt: str,
        profile: ModelProfile,
        routing: dict[str, Any],
        job_id: str | None,
    ) -> None:
        from mantis.core.permissions import PermissionRequiredError

        if job_id is None or self._explicit_model_requested:
            return
        if routing.get("strategy") != "auto_plan_router":
            return

        cheapest = self.router.route_cheapest()
        selected_cost = profile.cost_per_1k_input + profile.cost_per_1k_output
        cheapest_cost = cheapest.cost_per_1k_input + cheapest.cost_per_1k_output
        is_costly_upgrade = selected_cost > (cheapest_cost * 2)
        if not is_costly_upgrade or profile.name == cheapest.name:
            return

        tool_input = {
            "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
            "model": profile.name,
        }
        approved = self.approval_store.find_approved(
            session_id=self.session_id,
            job_id=job_id,
            tool_name="model_escalation",
            tool_input=tool_input,
        )
        if approved is not None:
            self.approval_store.update(approved.id, status="used")
            return

        pending = self.approval_store.find_pending(
            session_id=self.session_id,
            job_id=job_id,
            tool_name="model_escalation",
            tool_input=tool_input,
        )
        if pending is None:
            pending = self.approval_store.create(
                session_id=self.session_id,
                job_id=job_id,
                tool_name="model_escalation",
                tool_input=tool_input,
                risk_level="MEDIUM",
                preview={
                    "kind": "message",
                    "message": (
                        f"Escalate to {profile.name} for {routing.get('task_type', 'unknown')} "
                        f"work. Estimated price is ${selected_cost:.4f}/1k tokens vs "
                        f"${cheapest_cost:.4f}/1k on {cheapest.name}."
                    ),
                },
                kind="model_escalation",
                prompt=prompt,
                profile={
                    "name": profile.name,
                    "base_url": profile.base_url,
                    "input_cost": profile.cost_per_1k_input,
                    "output_cost": profile.cost_per_1k_output,
                },
                routing=routing,
            )

        raise PermissionRequiredError(
            approval_id=pending.id,
            tool_name="model_escalation",
            tool_input=tool_input,
            risk_level="MEDIUM",
        )

    async def _run_chat(self, prompt: str, job_id: str | None = None) -> str:
        self.permission_manager.set_context(session_id=self.session_id, job_id=job_id)
        profile, routing = self._resolve_model_for_prompt(prompt)
        self._require_api_key(profile)
        self._maybe_require_model_escalation_approval(prompt, profile, routing, job_id)
        if profile.name != self.model_adapter.model:
            self.model_adapter = self._create_model_adapter(profile)
            self.query_engine.model_adapter = self.model_adapter

        project_instructions = self._load_project_instructions()
        system_prompt = build_system_prompt(
            project_instructions=self._load_project_instructions()
        )
        execution_details: dict[str, Any] | None = None
        if self._should_use_orchestrator(routing):
            plan = build_execution_plan(prompt, cwd=self.project_dir)
            orchestrator = CoordinatorOrchestrator(
                model_adapter=self.model_adapter,
                tool_registry=self.tool_registry,
                project_instructions=project_instructions,
            )
            orchestration = await orchestrator.execute(prompt, plan)
            response = orchestration.output
            routing = {
                **routing,
                "execution_mode": "coordinator_worker_verifier",
                "verification_verdict": orchestration.verification.verdict,
                "verification_reason": orchestration.verification.reason,
                "revised": orchestration.revised,
            }
            execution_details = {
                "execution_mode": "coordinator_worker_verifier",
                "task_count": len(plan.tasks),
                "tasks": [
                    {
                        "title": task.title,
                        "task_type": task.task_type,
                        "file_targets": task.file_targets,
                        "status": "done",
                    }
                    for task in plan.tasks
                ],
                "verifier": {
                    "verdict": orchestration.verification.verdict,
                    "reason": orchestration.verification.reason,
                    "missing": orchestration.verification.missing,
                    "revised": orchestration.revised,
                },
            }
        else:
            response = await self.query_engine.run_agentic(prompt, system_prompt=system_prompt)
            execution_details = self.query_engine.last_run_details
        self.last_stats = self._build_stats(profile=profile, routing=routing, execution=execution_details)
        self.session_record = self.session_store.append(
            self.session_id,
            {
                "prompt": prompt,
                "response": response,
                "model": self.last_stats.get("model"),
                "routing": routing,
            },
            last_stats=self.last_stats,
        )
        return response

    def _should_use_orchestrator(self, routing: dict[str, Any]) -> bool:
        return bool(
            routing.get("needs_escalation")
            or routing.get("task_count", 1) > 1
            or routing.get("file_count", 0) >= 3
        )

    async def _resume_approval(self, approval_id: str, job_id: str | None = None) -> str:
        self._require_api_key()
        self.permission_manager.set_context(session_id=self.session_id, job_id=job_id)
        response = await self.query_engine.resume_from_approval(approval_id)
        self.last_stats = self._build_stats()
        self.session_record = self.session_store.append(
            self.session_id,
            {
                "approval_id": approval_id,
                "response": response,
                "model": self.last_stats.get("model"),
                "routing": self.last_stats.get("routing"),
            },
            last_stats=self.last_stats,
        )
        return response

    def _build_stats(
        self,
        profile: ModelProfile | None = None,
        routing: dict[str, Any] | None = None,
        execution: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        total_input = self.model_adapter.total_input_tokens
        total_output = self.model_adapter.total_output_tokens
        stats = {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "cost": self.model_adapter.total_cost_usd,
            "budget_usd": self.model_adapter.max_budget_usd,
            "remaining_budget_usd": self.model_adapter.remaining_budget_usd,
            "model": self.model_adapter.model,
            "provider": self._provider_from_base_url(self.model_adapter.base_url),
        }
        if profile is not None:
            stats["model"] = profile.name
            stats["provider"] = self._provider_from_base_url(profile.base_url)
        if routing is not None:
            stats["routing"] = routing
        if execution is not None:
            stats["execution"] = execution
        return stats

    def run(self, prompt: str) -> str:
        return asyncio.run(self._run_chat(prompt))

    def stream_chat(self, prompt: str):
        # The current CLI prints chunks as they arrive. For now, we yield the
        # final response as a single chunk to keep the interface stable.
        yield self.run(prompt)

    def list_models(self) -> dict[str, dict[str, Any]]:
        return {
            model.name: {
                "provider": self._provider_from_base_url(model.base_url),
                "intelligence_score": model.intelligence_score,
                "cost_per_1k_tokens": model.cost_per_1k_input + model.cost_per_1k_output,
                "context_window": model.context_window,
            }
            for model in self.router.list_models()
        }

    def list_tools(self) -> dict[str, dict[str, Any]]:
        return {
            tool.name: {"description": tool.description, "parameters": tool.parameters}
            for tool in self.tool_registry.list_all()
        }


App = MantisApp
