import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
import tempfile
import os
from pathlib import Path

from mantis.core.tool_registry import ToolRegistry
from mantis.core.context_manager import ContextManager
from mantis.memory.store import MemoryStore
from mantis.skills.loader import SkillLoader
from mantis.core.model_adapter import ModelAdapter
from mantis.core.hooks import HookManager, HookResult, Decision


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

        result = asyncio.get_event_loop().run_until_complete(
            registry.execute("add", {"a": 5, "b": 3})
        )
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
