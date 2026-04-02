import asyncio
import os
from typing import Any

from mantis.core.model_adapter import ModelAdapter
from mantis.core.query_engine import QueryEngine
from mantis.core.router import ModelProfile, ModelRouter
from mantis.core.tool_registry import ToolRegistry
from mantis.tools.builtins import register_builtins


DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"


class MantisApp:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.last_stats: dict[str, Any] = {}
        self.router = self._build_router()
        self.tool_registry = ToolRegistry()
        register_builtins(self.tool_registry)

        profile = self._select_model_profile()
        self.model_adapter = ModelAdapter(
            base_url=profile.base_url,
            api_key=profile.api_key,
            model=profile.name,
            max_tokens=self.config.get("max_tokens", 4096),
        )
        self.query_engine = QueryEngine(
            model_adapter=self.model_adapter,
            tool_registry=self.tool_registry,
            max_iterations=self.config.get("max_iterations", 25),
        )

    def _build_router(self) -> ModelRouter:
        router = ModelRouter()
        api_key = self.config.get("api_key") or os.environ.get("MANTIS_API_KEY", "")
        base_url = self.config.get("base_url") or os.environ.get(
            "MANTIS_BASE_URL",
            DEFAULT_BASE_URL,
        )
        model_name = self.config.get("model") or os.environ.get("MANTIS_MODEL", DEFAULT_MODEL)
        provider = self._provider_from_base_url(base_url)

        router.add_model(
            ModelProfile(
                name=model_name,
                base_url=base_url,
                api_key=api_key,
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
                api_key=api_key,
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
                api_key=api_key,
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
                api_key=api_key,
                intelligence_score=10,
                cost_per_1k_input=0.003,
                cost_per_1k_output=0.015,
                context_window=200000,
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
        if "dashscope" in host or "alibaba" in host:
            return "alibaba"
        if "ollama" in host:
            return "ollama"
        return "openai-compatible"

    def _select_model_profile(self) -> ModelProfile:
        configured = self.config.get("model")
        if configured:
            for model in self.router.list_models():
                if model.name == configured:
                    return model
        complexity = self.config.get("complexity", "medium")
        return self.router.route(complexity)

    def _require_api_key(self) -> None:
        if self.model_adapter.api_key:
            return
        raise ValueError(
            "No API key configured. Set MANTIS_API_KEY or pass --api-key."
        )

    async def _run_chat(self, prompt: str) -> str:
        self._require_api_key()
        response = await self.query_engine.run(prompt)
        self.last_stats = self._build_stats()
        return response

    def _build_stats(self) -> dict[str, Any]:
        total_input = self.model_adapter.total_input_tokens
        total_output = self.model_adapter.total_output_tokens
        return {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "cost": self.model_adapter.total_cost_usd,
        }

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
