import asyncio
import json
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Callable, Any
from enum import Enum


class Decision(Enum):
    ALLOW = "allow"
    BLOCK = "block"


@dataclass
class HookResult:
    decision: Decision
    reason: str
    modified_input: Optional[Dict[str, Any]] = None


class HookManager:
    def __init__(self):
        self.hooks = {
            "pre_tool_use": [],
            "post_tool_use": [],
            "stop": []
        }

    def register(self, event: str, hook: Callable):
        """Register a hook for an event type."""
        if event not in self.hooks:
            raise ValueError(f"Unknown event type: {event}")
        self.hooks[event].append(hook)

    async def run_pre_tool(self, tool_name: str, tool_input: dict) -> HookResult:
        """Run pre-tool hooks and return whether to allow execution."""
        for hook in self.hooks["pre_tool_use"]:
            result = await self._run_hook_safely(hook, tool_name=tool_name, tool_input=tool_input)
            if isinstance(result, HookResult):
                if result.decision == Decision.BLOCK:
                    return result
                elif result.decision == Decision.ALLOW and result.modified_input is not None:
                    tool_input = result.modified_input
        
        return HookResult(decision=Decision.ALLOW, reason="All pre-tool hooks passed", modified_input=tool_input)

    async def run_post_tool(self, tool_name: str, tool_input: dict, tool_output: str) -> None:
        """Run post-tool hooks."""
        for hook in self.hooks["post_tool_use"]:
            await self._run_hook_safely(hook, tool_name=tool_name, tool_input=tool_input, tool_output=tool_output)

    async def run_stop(self, reason: str, last_message: str) -> HookResult:
        """Run stop hooks and return whether to allow stopping."""
        for hook in self.hooks["stop"]:
            result = await self._run_hook_safely(hook, reason=reason, last_message=last_message)
            if isinstance(result, HookResult) and result.decision == Decision.BLOCK:
                return result
        
        return HookResult(decision=Decision.ALLOW, reason="All stop hooks passed")

    async def _run_hook_safely(self, hook, **kwargs):
        """Safely run a hook function, handling both sync and async."""
        try:
            if hasattr(hook, '__call__'):
                if asyncio.iscoroutinefunction(hook):
                    return await hook(**kwargs)
                else:
                    return hook(**kwargs)
        except Exception as e:
            print(f"Error running hook: {e}")
            return HookResult(decision=Decision.ALLOW, reason=f"Hook failed: {e}")

    def load_from_config(self, config: dict):
        """Load hooks from a config dict with command hooks."""
        hooks_config = config.get("hooks", {})
        
        for event_type, hooks in hooks_config.items():
            if event_type not in self.hooks:
                continue
            
            for hook_config in hooks:
                if "command" in hook_config:
                    command = hook_config["command"]
                    hook_func = self._create_command_hook(command)
                    self.register(event_type, hook_func)

    def _create_command_hook(self, command: str):
        """Create a hook function that runs a command."""
        def command_hook(**kwargs):
            # Prepare input data as JSON
            input_data = json.dumps(kwargs, ensure_ascii=False)
            
            # Run the command with JSON input
            result = subprocess.run(
                command,
                input=input_data,
                text=True,
                shell=True,
                capture_output=True
            )
            
            # Determine decision based on exit code
            if result.returncode == 0:
                decision = Decision.ALLOW
            elif result.returncode == 2:
                decision = Decision.BLOCK
            else:
                # For any other exit code, default to allow but log error
                print(f"Command hook exited with code {result.returncode}: {result.stderr}")
                return HookResult(decision=Decision.ALLOW, reason="Command hook error")
            
            # Try to parse the command's output as JSON to get additional details
            output_str = result.stdout.strip()
            modified_input = None
            
            if output_str:
                try:
                    output_json = json.loads(output_str)
                    if isinstance(output_json, dict):
                        modified_input = output_json.get("modified_input")
                except json.JSONDecodeError:
                    pass  # Ignore if output isn't valid JSON
            
            reason = f"Command hook returned {result.returncode}"
            return HookResult(decision=decision, reason=reason, modified_input=modified_input)
        
        return command_hook

