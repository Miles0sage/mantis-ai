from pathlib import Path

from mantis.tools.ast_extractor import build_edit_context, extract_symbol, extract_symbols, replace_symbol


def test_extract_symbols_includes_classes_and_methods(tmp_path):
    path = tmp_path / "sample.py"
    path.write_text(
        "import os\n\n"
        "@decorator\n"
        "def top():\n"
        "    return 1\n\n"
        "class Thing:\n"
        "    def method(self):\n"
        "        return 2\n"
    )

    symbols = extract_symbols(str(path))
    names = {symbol["name"]: symbol for symbol in symbols}

    assert "top" in names
    assert "Thing" in names
    assert "method" in names["Thing"]["source"]
    assert names["top"]["start_line"] == 3


def test_extract_and_replace_symbol(tmp_path):
    path = tmp_path / "sample.py"
    path.write_text("def top():\n    return 1\n")

    assert "return 1" in (extract_symbol(str(path), "top") or "")
    assert replace_symbol(str(path), "top", "def top():\n    return 2")
    assert "return 2" in path.read_text()


def test_build_edit_context_includes_imports_and_relevant_symbol(tmp_path):
    path = tmp_path / "sample.py"
    path.write_text(
        "import os\nfrom pathlib import Path\n\n"
        "def alpha():\n    return 'a'\n\n"
        "def planner_fix():\n    return 'rollback'\n"
    )

    context = build_edit_context(str(path), "fix planner rollback behavior")

    assert "Imports / header" in context
    assert "planner_fix" in context
    assert "import os" in context
