import asyncio
import time
from dataclasses import dataclass
from typing import Dict, List

from mantis.core.context_manager import ContextManager
from mantis.core.hooks import HookManager
from mantis.core.model_adapter import ModelAdapter
from mantis.core.permissions import PermissionManager
from mantis.core.query_engine import QueryEngine
from mantis.core.tool_registry import ToolRegistry


@dataclass
class AgentResult:
    agent_id: str
    task: str
    output: str
    status: str
    duration_ms: float
    token_usage: dict


class AgentSpawner:
    def __init__(
        self,
        model_adapter: ModelAdapter,
        tool_registry: ToolRegistry,
        worker_model_adapter: ModelAdapter | None = None,
    ):
        self.model_adapter = model_adapter
        self.tool_registry = tool_registry
        # Worker adapter uses a cheaper model; falls back to main adapter if not set
        self.worker_model_adapter = worker_model_adapter or model_adapter
        self._active_agents: Dict[str, asyncio.Task] = {}
        self._agent_results: Dict[str, AgentResult] = {}

    def _clone_model_adapter(self) -> ModelAdapter:
        return ModelAdapter(
            base_url=self.model_adapter.base_url,
            api_key=self.model_adapter.api_key,
            model=self.model_adapter.model,
            max_tokens=self.model_adapter.max_tokens,
            cost_per_1k_input=self.model_adapter.cost_per_1k_input,
            cost_per_1k_output=self.model_adapter.cost_per_1k_output,
            max_budget_usd=self.model_adapter.max_budget_usd,
        )

    def _clone_worker_adapter(self) -> ModelAdapter:
        src = self.worker_model_adapter
        return ModelAdapter(
            base_url=src.base_url,
            api_key=src.api_key,
            model=src.model,
            max_tokens=src.max_tokens,
            cost_per_1k_input=src.cost_per_1k_input,
            cost_per_1k_output=src.cost_per_1k_output,
            max_budget_usd=src.max_budget_usd,
        )

    async def spawn(self, task: str, system_prompt: str | None = None, agent_id: str | None = None) -> AgentResult:
        if agent_id is None:
            agent_id = f"subagent_{int(time.time() * 1000)}"

        start_time = time.time()
        query_engine = QueryEngine(
            model_adapter=self._clone_worker_adapter(),
            tool_registry=self.tool_registry,
            max_iterations=8,
            context_manager=ContextManager(max_tokens=128000),
            hook_manager=HookManager(),
            permission_manager=PermissionManager(mode="yolo"),
        )

        async def run_agent():
            try:
                output = await query_engine.run_agentic(task, system_prompt=system_prompt)
                duration = (time.time() - start_time) * 1000
                result = AgentResult(
                    agent_id=agent_id,
                    task=task,
                    output=output,
                    status="completed",
                    duration_ms=duration,
                    token_usage={
                        "input_tokens": query_engine.model_adapter.total_input_tokens,
                        "output_tokens": query_engine.model_adapter.total_output_tokens,
                        "cost": query_engine.model_adapter.total_cost_usd,
                    },
                )
                self._agent_results[agent_id] = result
                return result
            except Exception as exc:
                duration = (time.time() - start_time) * 1000
                result = AgentResult(
                    agent_id=agent_id,
                    task=task,
                    output=str(exc),
                    status="failed",
                    duration_ms=duration,
                    token_usage={},
                )
                self._agent_results[agent_id] = result
                return result

        task_obj = asyncio.create_task(run_agent())
        self._active_agents[agent_id] = task_obj
        try:
            result = await asyncio.wait_for(task_obj, timeout=300.0)
        except asyncio.TimeoutError:
            task_obj.cancel()
            try:
                await task_obj
            except asyncio.CancelledError:
                pass
            duration = (time.time() - start_time) * 1000
            result = AgentResult(
                agent_id=agent_id,
                task=task,
                output="Task timed out",
                status="failed",
                duration_ms=duration,
                token_usage={},
            )
            self._agent_results[agent_id] = result
        finally:
            self._active_agents.pop(agent_id, None)
        return result

    async def spawn_parallel(self, tasks: List[str], system_prompt: str | None = None) -> List[AgentResult]:
        coros = [
            self.spawn(task, system_prompt=system_prompt, agent_id=f"parallel_{int(time.time() * 1000)}_{index}")
            for index, task in enumerate(tasks)
        ]
        results = await asyncio.gather(*coros, return_exceptions=True)
        processed: list[AgentResult] = []
        for result in results:
            if isinstance(result, Exception):
                processed.append(
                    AgentResult(
                        agent_id="unknown",
                        task="unknown",
                        output=str(result),
                        status="failed",
                        duration_ms=0.0,
                        token_usage={},
                    )
                )
            else:
                processed.append(result)
        return processed

    def list_running(self) -> List[str]:
        completed = [agent_id for agent_id, task in self._active_agents.items() if task.done()]
        for agent_id in completed:
            self._active_agents.pop(agent_id, None)
        return list(self._active_agents.keys())
