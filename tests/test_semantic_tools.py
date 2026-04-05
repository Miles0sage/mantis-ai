import asyncio

from mantis.core.tool_registry import ToolRegistry
from mantis.tools.builtins import register_builtins


def _make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_builtins(registry)
    return registry


def test_semantic_python_tools_are_registered():
    registry = _make_registry()
    names = {tool.name for tool in registry.list_all()}

    assert "list_python_symbols" in names
    assert "read_python_symbol" in names
    assert "replace_python_symbol" in names
    assert "build_python_edit_context" in names
    assert "list_js_symbols" in names
    assert "read_js_symbol" in names
    assert "build_js_edit_context" in names


def test_list_and_read_python_symbol(tmp_path):
    path = tmp_path / "sample.py"
    path.write_text(
        "import os\n\n"
        "def alpha():\n"
        "    return 1\n\n"
        "class Beta:\n"
        "    pass\n",
        encoding="utf-8",
    )
    registry = _make_registry()

    symbols = asyncio.run(
        registry.get("list_python_symbols").handler(file_path=str(path))
    )
    top_level_names = {symbol["name"] for symbol in symbols}
    assert top_level_names == {"alpha", "Beta"}

    source = asyncio.run(
        registry.get("read_python_symbol").handler(
            file_path=str(path),
            symbol_name="alpha",
        )
    )
    assert "def alpha" in source


def test_replace_python_symbol_and_build_context(tmp_path):
    path = tmp_path / "sample.py"
    path.write_text(
        "import os\n\n"
        "def planner_fix():\n"
        "    return 'before'\n\n"
        "def untouched():\n"
        "    return 'same'\n",
        encoding="utf-8",
    )
    registry = _make_registry()

    result = asyncio.run(
        registry.get("replace_python_symbol").handler(
            file_path=str(path),
            symbol_name="planner_fix",
            new_source="def planner_fix():\n    return 'after'",
        )
    )
    assert result["success"] is True
    assert "return 'after'" in path.read_text(encoding="utf-8")
    assert "return 'same'" in path.read_text(encoding="utf-8")

    context = asyncio.run(
        registry.get("build_python_edit_context").handler(
            file_path=str(path),
            task_description="fix planner behavior",
        )
    )
    assert "planner_fix" in context
    assert "Imports / header" in context


def test_list_and_read_js_symbol(tmp_path):
    path = tmp_path / "sample.ts"
    path.write_text(
        "export class AuthService {\n"
        "  login() { return true; }\n"
        "}\n\n"
        "export function createSession(userId: string) {\n"
        "  return { userId };\n"
        "}\n",
        encoding="utf-8",
    )
    registry = _make_registry()

    symbols = asyncio.run(
        registry.get("list_js_symbols").handler(file_path=str(path))
    )
    top_level_names = {symbol["name"] for symbol in symbols}
    assert top_level_names == {"AuthService", "createSession"}

    source = asyncio.run(
        registry.get("read_js_symbol").handler(
            file_path=str(path),
            symbol_name="createSession",
        )
    )
    assert "createSession" in source
    assert "userId" in source


def test_build_js_edit_context(tmp_path):
    path = tmp_path / "sample.js"
    path.write_text(
        "class BillingService {\n"
        "  charge() { return 'ok'; }\n"
        "}\n\n"
        "const createInvoice = (amount) => ({ amount });\n",
        encoding="utf-8",
    )
    registry = _make_registry()
    context = asyncio.run(
        registry.get("build_js_edit_context").handler(
            file_path=str(path),
            task_description="add refund support",
        )
    )
    assert "BillingService" in context
    assert "createInvoice" in context
    assert "add refund support" in context
