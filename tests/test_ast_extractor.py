"""Tests for mantis.tools.ast_extractor."""
import os
import tempfile
import textwrap

import pytest

from mantis.tools.ast_extractor import (
    build_edit_context,
    extract_symbol,
    extract_symbols,
    replace_symbol,
)

SAMPLE_CODE = textwrap.dedent("""\
    import os

    def greet(name):
        return f"Hello, {name}"

    class Greeter:
        def say_hi(self):
            return "hi"

    async def async_fetch(url):
        pass
""")


@pytest.fixture
def sample_file(tmp_path):
    """Write SAMPLE_CODE to a temp .py file and return its path."""
    p = tmp_path / "sample.py"
    p.write_text(SAMPLE_CODE)
    return str(p)


# -- extract_symbols ----------------------------------------------------------

def test_extract_symbols_returns_all_top_level(sample_file):
    symbols = extract_symbols(sample_file)
    names = [s["name"] for s in symbols]
    assert "greet" in names
    assert "Greeter" in names
    assert "async_fetch" in names


def test_extract_symbols_types(sample_file):
    symbols = extract_symbols(sample_file)
    by_name = {s["name"]: s for s in symbols}
    assert by_name["greet"]["type"] == "function"
    assert by_name["Greeter"]["type"] == "class"
    assert by_name["async_fetch"]["type"] == "function"


def test_extract_symbols_bad_file():
    assert extract_symbols("/nonexistent/path.py") == []


def test_extract_symbols_syntax_error(tmp_path):
    p = tmp_path / "bad.py"
    p.write_text("def broken(:\n")
    assert extract_symbols(str(p)) == []


# -- extract_symbol -----------------------------------------------------------

def test_extract_symbol_found(sample_file):
    src = extract_symbol(sample_file, "greet")
    assert src is not None
    assert "Hello" in src


def test_extract_symbol_not_found(sample_file):
    assert extract_symbol(sample_file, "nonexistent") is None


# -- replace_symbol -----------------------------------------------------------

def test_replace_symbol_success(sample_file):
    new_fn = 'def greet(name):\n    return f"Goodbye, {name}"'
    assert replace_symbol(sample_file, "greet", new_fn) is True

    src = extract_symbol(sample_file, "greet")
    assert "Goodbye" in src


def test_replace_symbol_not_found(sample_file):
    assert replace_symbol(sample_file, "nonexistent", "pass") is False


def test_replace_symbol_bad_file():
    assert replace_symbol("/nonexistent/path.py", "x", "pass") is False


# -- build_edit_context -------------------------------------------------------

def test_build_edit_context_relevant(sample_file):
    ctx = build_edit_context(sample_file, "greet the user")
    assert "greet" in ctx
    # Should include the file header
    assert "import os" in ctx


def test_build_edit_context_no_match_shows_all(sample_file):
    ctx = build_edit_context(sample_file, "zzzzz_no_match")
    # When nothing matches keywords, all symbols are shown
    assert "greet" in ctx
    assert "Greeter" in ctx


def test_build_edit_context_bad_file():
    ctx = build_edit_context("/nonexistent/path.py", "anything")
    assert "Error" in ctx
