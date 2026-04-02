# mantis/app.py — wires all MantisAI components into one working agent
import os
import asyncio
from mantis.core.query_engine import QueryEngine
from mantis.core.model_adapter import ModelAdapter
from mantis.core.tool_registry import ToolRegistry
from mantis.core.hooks import HookManager
from mantis.core.compressor import ContextCompressor
from mantis.core.router import ModelRouter, ModelProfile
from mantis.core.system_prompt import build_system_prompt
from mantis.tools.builtins import register_builtins


class MantisApp:
    def __init__(self, config: dict = None):
        self.config = config or {}

        # Load settings from config or env
        self.api_key = (
            self.config.get("api_key")
            or os.getenv("MANTIS_API_KEY")
            or os.getenv("OPENAI_API_KEY", "")
        )
        self.base_url = (
            self.config.get("base_url")
            or os.getenv("MANTIS_BASE_URL", "https://api.openai.com/v1")
        )
        self.model_name = (
            self.config.get("model")
            or os.getenv("MANTIS_MODEL", "gpt-4o-mini")
        )
        self.max_iterations = int(self.config.get("max_iterations", 25))

        # Model router
        self.router = ModelRouter()
        self.router.add_model(ModelProfile(
            name=self.model_name,
            base_url=self.base_url,
            api_key=self.api_key,
            intelligence_score=10,
            cost_per_1k_input=0.00015,
            cost_per_1k_output=0.0006,
            context_window=128000,
            supports_tools=True,
            supports_streaming=True,
        ))

        # Tool registry with builtins
        self.registry = ToolRegistry()
        register_builtins(self.registry)

        # Hooks
        self.hooks = HookManager()

        # Compressor
        self.compressor = ContextCompressor()

        # Model adapter
        self.adapter = ModelAdapter(
            base_url=self.base_url,
            api_key=self.api_key,
            model=self.model_name,
        )

        # Query engine
        self.engine = QueryEngine(
            model_adapter=self.adapter,
            tool_registry=self.registry,
            max_iterations=self.max_iterations,
        )

        # System prompt
        project_instructions = None
        if os.path.exists("MANTIS.md"):
            with open("MANTIS.md") as f:
                project_instructions = f.read()

        skills_summary = None
        tool_names = [t.name for t in self.registry.list_all()]
        if tool_names:
            skills_summary = "Available tools: " + ", ".join(tool_names)

        self.system_prompt = build_system_prompt(
            project_instructions=project_instructions,
            skills_summary=skills_summary,
        )

    async def run(self, prompt: str) -> str:
        """Run agent on a single prompt and return the response."""
        result = await self.engine.run(prompt, system_prompt=self.system_prompt)
        return result

    async def chat(self) -> None:
        """Interactive REPL loop."""
        print("\033[92m╭─ MantisAI Agent\033[0m")
        print("\033[92m│\033[0m  Model: \033[93m{}\033[0m".format(self.model_name))
        print("\033[92m│\033[0m  Tools: \033[93m{}\033[0m".format(len(self.registry.list_all())))
        print("\033[92m╰─\033[0m Type 'exit' to quit\n")

        while True:
            try:
                user_input = input("\033[96m> \033[0m").strip()

                if not user_input:
                    continue
                if user_input.lower() in ("exit", "quit", "q"):
                    print("\033[90mGoodbye.\033[0m")
                    break

                response = await self.run(user_input)
                print(f"\n\033[92m{response}\033[0m\n")

                # Show token usage
                if hasattr(self.adapter, 'total_input_tokens'):
                    inp = getattr(self.adapter, 'total_input_tokens', 0)
                    out = getattr(self.adapter, 'total_output_tokens', 0)
                    if inp or out:
                        print(f"\033[90m[tokens: {inp} in / {out} out]\033[0m\n")

            except KeyboardInterrupt:
                print("\n\033[90mGoodbye.\033[0m")
                break
            except Exception as e:
                print(f"\033[91mError: {e}\033[0m\n")


def main():
    """Entry point for CLI."""
    app = MantisApp()
    asyncio.run(app.chat())
