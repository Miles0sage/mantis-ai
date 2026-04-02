import asyncio
import time
from dataclasses import dataclass
from typing import Dict, List, Optional
from mantis.core.query_engine import QueryEngine
from mantis.core.model_adapter import ModelAdapter
from mantis.core.tool_registry import ToolRegistry


@dataclass
class AgentResult:
    agent_id: str
    task: str
    output: str
    status: str  # 'completed' or 'failed'
    duration_ms: float
    token_usage: dict


class AgentSpawner:
    def __init__(self, model_adapter: ModelAdapter, tool_registry: ToolRegistry):
        self.model_adapter = model_adapter
        self.tool_registry = tool_registry
        self._active_agents: Dict[str, asyncio.Task] = {}
        self._agent_results: Dict[str, AgentResult] = {}

    async def spawn(self, task: str, agent_id: str = None) -> AgentResult:
        if agent_id is None:
            agent_id = f"subagent_{int(time.time() * 1000)}"
        
        start_time = time.time()
        
        # Create a new QueryEngine for this agent
        query_engine = QueryEngine(
            model_adapter=self.model_adapter,
            tool_registry=self.tool_registry
        )
        
        async def run_agent():
            try:
                output = await query_engine.process_query(task)
                duration = (time.time() - start_time) * 1000
                token_usage = getattr(query_engine, 'token_usage', {})
                
                result = AgentResult(
                    agent_id=agent_id,
                    task=task,
                    output=output,
                    status='completed',
                    duration_ms=duration,
                    token_usage=token_usage
                )
                self._agent_results[agent_id] = result
                return result
            except Exception as e:
                duration = (time.time() - start_time) * 1000
                result = AgentResult(
                    agent_id=agent_id,
                    task=task,
                    output=str(e),
                    status='failed',
                    duration_ms=duration,
                    token_usage={}
                )
                self._agent_results[agent_id] = result
                return result
        
        # Create and track the task
        task_obj = asyncio.create_task(run_agent())
        self._active_agents[agent_id] = task_obj
        
        try:
            # Wait for completion with timeout
            result = await asyncio.wait_for(task_obj, timeout=300.0)  # 5 minutes
        except asyncio.TimeoutError:
            # Cancel the task if it times out
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
                status='failed',
                duration_ms=duration,
                token_usage={}
            )
            self._agent_results[agent_id] = result
        
        # Clean up tracking
        if agent_id in self._active_agents:
            del self._active_agents[agent_id]
        
        return result

    async def spawn_parallel(self, tasks: List[str]) -> List[AgentResult]:
        tasks_to_run = []
        
        for i, task in enumerate(tasks):
            agent_id = f"parallel_subagent_{int(time.time() * 1000)}_{i}"
            task_coro = self.spawn(task, agent_id)
            tasks_to_run.append(task_coro)
        
        results = await asyncio.gather(*tasks_to_run, return_exceptions=True)
        
        # Handle any exceptions that occurred during execution
        processed_results = []
        for result in results:
            if isinstance(result, Exception):
                # In case of an exception creating the agent
                processed_results.append(AgentResult(
                    agent_id="unknown",
                    task="unknown",
                    output=str(result),
                    status='failed',
                    duration_ms=0,
                    token_usage={}
                ))
            else:
                processed_results.append(result)
        
        return processed_results

    def list_running(self) -> List[str]:
        # Clean up completed tasks
        completed_agents = []
        for agent_id, task in self._active_agents.items():
            if task.done():
                completed_agents.append(agent_id)
        
        for agent_id in completed_agents:
            del self._active_agents[agent_id]
        
        return list(self._active_agents.keys())
