import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
import tempfile
import os
from pathlib import Path

from mantis.app import MantisApp
from mantis.core.tool_registry import ToolRegistry
from mantis.core.context_manager import ContextManager
from mantis.memory.store import MemoryStore
from mantis.skills.loader import SkillLoader
from mantis.core.model_adapter import BudgetExceededError, ModelAdapter
from mantis.core.hooks import HookManager, HookResult, Decision
from mantis.core.router import ModelProfile, ModelRouter


class TestToolRegistry:
    def test_register_tool(self):
        registry = ToolRegistry()

        async def sample_tool():
            return "result"

        tool = registry.register("sample", "Sample tool description", {}, sample_tool)

        assert tool.name == "sample"
        assert tool.description == "Sample tool description"
        assert tool.handler == sample_tool

    def test_list_tools(self):
        registry = ToolRegistry()

        async def tool1():
            pass

        async def tool2():
            pass

        registry.register("tool1", "Tool 1 description", {}, tool1)
        registry.register("tool2", "Tool 2 description", {}, tool2)

        tools = registry.list_all()
        assert len(tools) == 2
        names = [t.name for t in tools]
        assert "tool1" in names
        assert "tool2" in names

    def test_execute_tool(self):
        registry = ToolRegistry()

        async def add_numbers(a: int, b: int) -> int:
            return a + b

        registry.register("add", "Add two numbers", {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}}, add_numbers)

        result = asyncio.run(registry.execute("add", {"a": 5, "b": 3}))
        assert result == "8"

    def test_search_tools(self):
        registry = ToolRegistry()

        async def calculate_sum(numbers: list) -> int:
            return sum(numbers)

        async def multiply(a: int, b: int) -> int:
            return a * b

        registry.register("sum", "Calculate sum of numbers", {}, calculate_sum)
        registry.register("multiply", "Multiply two numbers", {}, multiply)

        results = registry.search("sum")
        assert "sum" in [r.name for r in results]

    def test_list_schemas_format(self):
        registry = ToolRegistry()

        async def sample_tool(x: int, y: str) -> dict:
            return {"x": x, "y": y}

        params = {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "string"}
            }
        }
        registry.register("sample", "Sample tool", params, sample_tool)

        schemas = registry.list_schemas()
        assert len(schemas) == 1
        schema = schemas[0]
        assert schema["function"]["name"] == "sample"
        assert "parameters" in schema["function"]
        assert "properties" in schema["function"]["parameters"]
        assert "x" in schema["function"]["parameters"]["properties"]
        assert "y" in schema["function"]["parameters"]["properties"]


class TestContextManager:
    def test_add_messages(self):
        context = ContextManager(max_tokens=1000)

        context.add_message("user", "Hello")
        context.add_message("assistant", "Hi there!")

        messages = context.get_messages()
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "Hi there!"

    def test_token_counting(self):
        context = ContextManager(max_tokens=1000)

        context.add_message("user", "Hello world")
        context.add_message("assistant", "Hi there!")

        count = context.token_count()
        assert count > 0  # Should be greater than 0

    def test_truncate_to_fit(self):
        context = ContextManager(max_tokens=10)  # Very small limit

        context.add_message("user", "A very long message that will definitely exceed our token limit")
        context.add_message("assistant", "Another long response that adds to the token count")

        context.truncate_to_fit()
        # After truncation, messages should be reduced (returns None, mutates in place)
        assert context.token_count() <= 10 or len(context.get_messages()) == 0

    def test_clear(self):
        context = ContextManager(max_tokens=1000)

        context.add_message("user", "Hello")
        context.add_message("assistant", "Hi there!")

        context.clear()

        messages = context.get_messages()
        assert len(messages) == 0


class TestMemoryStore:
    def test_save_and_recall(self, tmp_path):
        memory_store = MemoryStore(str(tmp_path))

        memory_store.save("test_id", "This is a test memory", {"category": "test"})

        recalled = memory_store.recall("test_id")
        assert recalled.content == "This is a test memory"
        assert recalled.metadata["category"] == "test"

    def test_search(self, tmp_path):
        memory_store = MemoryStore(str(tmp_path))

        memory_store.save("test1", "This is a sample memory for testing", {"category": "test"})
        memory_store.save("test2", "Another memory with different content", {"category": "other"})

        results = memory_store.search("testing")
        assert len(results) >= 1
        found_keys = [r.key for r in results]
        assert "test1" in found_keys

    def test_delete(self, tmp_path):
        memory_store = MemoryStore(str(tmp_path))

        memory_store.save("test_id", "This will be deleted", {})

        # Verify it exists before deletion
        recalled = memory_store.recall("test_id")
        assert recalled is not None

        memory_store.delete("test_id")

        # Verify it's gone after deletion
        recalled = memory_store.recall("test_id")
        assert recalled is None

    def test_list_all(self, tmp_path):
        memory_store = MemoryStore(str(tmp_path))

        memory_store.save("test1", "Content 1", {})
        memory_store.save("test2", "Content 2", {})

        all_memories = memory_store.list_all()
        assert len(all_memories) == 2
        keys = [m.key for m in all_memories]
        assert "test1" in keys
        assert "test2" in keys


class TestSkillLoader:
    def test_load_from_directory(self, tmp_path):
        # Create a temporary skill file with YAML frontmatter
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()

        skill_file = skill_dir / "sample_skill.md"
        skill_content = '''---
name: sample_skill
description: This is a sample skill
---
This is the skill content.
'''
        skill_file.write_text(skill_content)

        loader = SkillLoader(str(skill_dir))
        skills = loader.load_all()

        assert len(skills) == 1
        assert skills[0].name == "sample_skill"

    def test_search_skills(self, tmp_path):
        # Create temporary skill files with YAML frontmatter
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()

        skill_file1 = skill_dir / "add_numbers.md"
        skill_content1 = '''---
name: add_numbers
description: Add two numbers together
---
Adds numbers.
'''
        skill_file1.write_text(skill_content1)

        skill_file2 = skill_dir / "multiply_numbers.md"
        skill_content2 = '''---
name: multiply_numbers
description: Multiply two numbers
---
Multiplies numbers.
'''
        skill_file2.write_text(skill_content2)

        loader = SkillLoader(str(skill_dir))
        loader.load_all()

        results = loader.search("add")
        assert len(results) >= 1
        assert any(s.name == "add_numbers" for s in results)


class TestModelAdapter:
    def test_init_with_params(self):
        adapter = ModelAdapter(
            base_url="https://api.example.com",
            api_key="fake-key",
            model="gpt-4",
            max_tokens=1000
        )

        assert adapter.model == "gpt-4"
        assert adapter.api_key == "fake-key"
        assert adapter.base_url == "https://api.example.com"
        assert adapter.max_tokens == 1000

    def test_init_minimal_params(self):
        adapter = ModelAdapter(
            base_url="https://api.example.com",
            api_key="minimal-key",
            model="gpt-3.5-turbo"
        )

        assert adapter.model == "gpt-3.5-turbo"
        assert adapter.api_key == "minimal-key"

    @pytest.mark.asyncio
    async def test_budget_blocks_request_when_limit_reached(self):
        adapter = ModelAdapter(
            base_url="https://api.example.com",
            api_key="fake-key",
            model="gpt-4",
            max_budget_usd=0.001,
        )
        adapter._total_cost_usd = 0.001
        adapter._make_request = AsyncMock()

        with pytest.raises(BudgetExceededError):
            await adapter.chat([{"role": "user", "content": "hi"}])

        adapter._make_request.assert_not_called()

    def test_register_usage_tracks_remaining_budget(self):
        adapter = ModelAdapter(
            base_url="https://api.example.com",
            api_key="fake-key",
            model="gpt-4",
            cost_per_1k_input=1.0,
            cost_per_1k_output=2.0,
            max_budget_usd=1.0,
        )

        adapter._register_usage({"prompt_tokens": 100, "completion_tokens": 200})

        assert adapter.total_input_tokens == 100
        assert adapter.total_output_tokens == 200
        assert adapter.total_cost_usd == pytest.approx(0.5)
        assert adapter.remaining_budget_usd == pytest.approx(0.5)


class TestModelRouter:
    def _make_router(self):
        router = ModelRouter()
        router.add_model(
            ModelProfile(
                name="cheap",
                base_url="https://cheap.example/v1",
                api_key="k",
                intelligence_score=7,
                cost_per_1k_input=0.0001,
                cost_per_1k_output=0.0002,
                context_window=64000,
                supports_tools=True,
                supports_streaming=True,
            )
        )
        router.add_model(
            ModelProfile(
                name="mid",
                base_url="https://mid.example/v1",
                api_key="k",
                intelligence_score=10,
                cost_per_1k_input=0.0004,
                cost_per_1k_output=0.0008,
                context_window=128000,
                supports_tools=True,
                supports_streaming=True,
            )
        )
        router.add_model(
            ModelProfile(
                name="best",
                base_url="https://best.example/v1",
                api_key="k",
                intelligence_score=18,
                cost_per_1k_input=0.003,
                cost_per_1k_output=0.015,
                context_window=200000,
                supports_tools=True,
                supports_streaming=True,
            )
        )
        return router

    def test_route_for_plan_uses_cheapest_for_low_scope_docs(self):
        router = self._make_router()
        profile = router.route_for_plan(
            task_type="docs",
            complexity="low",
            file_count=0,
            task_count=1,
            needs_escalation=False,
        )
        assert profile.name == "cheap"

    def test_route_for_plan_uses_best_for_cross_file_code(self):
        router = self._make_router()
        profile = router.route_for_plan(
            task_type="feature",
            complexity="medium",
            file_count=2,
            task_count=1,
            needs_escalation=False,
        )
        assert profile.name == "best"

    def test_route_for_plan_uses_best_for_escalated_work(self):
        router = self._make_router()
        profile = router.route_for_plan(
            task_type="refactor",
            complexity="high",
            file_count=1,
            task_count=2,
            needs_escalation=True,
        )
        assert profile.name == "best"


class TestHookManager:
    def test_register_and_run_pre_hooks(self):
        hook_manager = HookManager()

        async def mock_hook(tool_name, tool_input):
            return HookResult(decision=Decision.ALLOW, reason="ok")

        hook_manager.register("pre_tool_use", mock_hook)

        result = asyncio.get_event_loop().run_until_complete(
            hook_manager.run_pre_tool("test_tool", {"param": "value"})
        )

        assert result.decision == Decision.ALLOW

    def test_register_and_run_post_hooks(self):
        hook_manager = HookManager()

        called = []

        async def mock_hook(tool_name, tool_input, tool_output):
            called.append(True)

        hook_manager.register("post_tool_use", mock_hook)

        asyncio.get_event_loop().run_until_complete(
            hook_manager.run_post_tool("test_tool", {"param": "value"}, "result")
        )

        assert len(called) == 1

    def test_multiple_hooks(self):
        hook_manager = HookManager()

        calls = []

        async def mock_hook1(tool_name, tool_input):
            calls.append("hook1")
            return HookResult(decision=Decision.ALLOW, reason="ok")

        async def mock_hook2(tool_name, tool_input):
            calls.append("hook2")
            return HookResult(decision=Decision.ALLOW, reason="ok")

        hook_manager.register("pre_tool_use", mock_hook1)
        hook_manager.register("pre_tool_use", mock_hook2)

        asyncio.get_event_loop().run_until_complete(
            hook_manager.run_pre_tool("test_tool", {"param": "value"})
        )

        assert len(calls) == 2


class TestMantisSemanticGuardrail:
    def test_blocks_raw_python_edit_tools(self):
        app = MantisApp({"api_key": "test-key"}, session_id="guardrail-test")

        result = asyncio.get_event_loop().run_until_complete(
            app.hook_manager.run_pre_tool(
                "edit_file",
                {
                    "file_path": "/tmp/example.py",
                    "old_string": "return 1",
                    "new_string": "return 2",
                },
            )
        )

        assert result.decision == Decision.BLOCK
        assert "replace_python_symbol" in result.reason

    def test_allows_non_python_edit_tools(self):
        app = MantisApp({"api_key": "test-key"}, session_id="guardrail-test")

        result = asyncio.get_event_loop().run_until_complete(
            app.hook_manager.run_pre_tool(
                "edit_file",
                {
                    "file_path": "/tmp/example.md",
                    "old_string": "a",
                    "new_string": "b",
                },
            )
        )

        assert result.decision == Decision.ALLOW


class TestMantisAppRouting:
    def test_should_use_orchestrator_for_complex_multi_file_work(self):
        app = object.__new__(MantisApp)

        assert app._should_use_orchestrator(
            {
                "needs_escalation": False,
                "task_count": 2,
                "file_count": 3,
                "complexity": "medium",
            }
        ) is True

    def test_should_not_use_orchestrator_for_simple_single_file_read(self):
        app = object.__new__(MantisApp)

        assert app._should_use_orchestrator(
            {
                "needs_escalation": False,
                "task_count": 2,
                "file_count": 1,
                "complexity": "low",
            }
        ) is False

    def test_read_only_fast_path_counts_python_test_functions(self, tmp_path):
        test_file = tmp_path / "sample_test_file.py"
        test_file.write_text(
            "def helper():\n    return 1\n\n"
            "def test_one():\n    assert True\n\n"
            "async def test_two():\n    assert True\n",
            encoding="utf-8",
        )

        app = MantisApp({"api_key": "test-key"}, project_dir=str(tmp_path), session_id="fast-path")
        result = asyncio.get_event_loop().run_until_complete(
            app._run_chat(
                "Read sample_test_file.py and reply only with the number of test functions in the file."
            )
        )

        assert result == "2"
        assert app.last_stats["routing"]["strategy"] == "local_fast_path"

    def test_read_only_fast_path_lists_python_test_functions(self, tmp_path):
        test_file = tmp_path / "sample_test_file.py"
        test_file.write_text(
            "def test_alpha():\n    assert True\n\n"
            "def test_beta():\n    assert True\n",
            encoding="utf-8",
        )

        app = MantisApp({"api_key": "test-key"}, project_dir=str(tmp_path), session_id="fast-path-list")
        result = asyncio.get_event_loop().run_until_complete(
            app._run_chat(
                "Read sample_test_file.py and reply only with the names of the test functions."
            )
        )

        assert result == "test_alpha\ntest_beta"
        assert app.last_stats["execution"]["execution_mode"] == "local_fast_path"

    def test_local_fast_path_updates_simple_return_value(self, tmp_path):
        target = tmp_path / "edit_me.py"
        target.write_text("def value():\n    return 1\n", encoding="utf-8")

        app = MantisApp({"api_key": "test-key"}, project_dir=str(tmp_path), session_id="fast-path-edit")
        result = asyncio.get_event_loop().run_until_complete(
            app._run_chat(
                f"Read {target.name}, change the return value from 1 to 2, verify the file was updated, and keep the final answer short."
            )
        )

        assert result == "File updated: return value changed from 1 to 2."
        assert target.read_text(encoding="utf-8") == "def value():\n    return 2\n"
        assert app.last_stats["routing"]["strategy"] == "local_fast_path"
