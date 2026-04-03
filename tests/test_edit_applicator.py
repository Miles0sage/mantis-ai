"""Tests for mantis.tools.edit_applicator."""
import os
import textwrap

import pytest

from mantis.tools.edit_applicator import apply_all_edits, apply_edit, parse_search_replace


# -- parse_search_replace -----------------------------------------------------

def test_parse_single_block():
    llm = textwrap.dedent("""\
        <<<<<<< SEARCH
        old line
        =======
        new line
        >>>>>>> REPLACE
    """)
    edits = parse_search_replace(llm)
    assert len(edits) == 1
    assert edits[0]["search"] == "old line"
    assert edits[0]["replace"] == "new line"
    assert edits[0]["file_path"] is None


def test_parse_with_file_path():
    llm = textwrap.dedent("""\
        <<<<<<< SEARCH
        file_path:src/main.py
        old code here
        =======
        new code here
        >>>>>>> REPLACE
    """)
    edits = parse_search_replace(llm)
    assert len(edits) == 1
    assert edits[0]["file_path"] == "src/main.py"
    assert edits[0]["search"] == "old code here"
    assert edits[0]["replace"] == "new code here"


def test_parse_multiple_blocks():
    llm = textwrap.dedent("""\
        <<<<<<< SEARCH
        alpha
        =======
        beta
        >>>>>>> REPLACE
        some text between
        <<<<<<< SEARCH
        gamma
        =======
        delta
        >>>>>>> REPLACE
    """)
    edits = parse_search_replace(llm)
    assert len(edits) == 2
    assert edits[0]["search"] == "alpha"
    assert edits[1]["search"] == "gamma"


def test_parse_empty_string():
    assert parse_search_replace("") == []


# -- apply_edit ---------------------------------------------------------------

def test_apply_edit_exact_match(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("hello world\n")
    assert apply_edit(str(f), "hello world", "goodbye world") is True
    assert f.read_text() == "goodbye world\n"


def test_apply_edit_file_not_found():
    assert apply_edit("/nonexistent/file.py", "a", "b") is False


def test_apply_edit_no_match(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("hello world\n")
    assert apply_edit(str(f), "zzz not here", "replacement") is False
    assert f.read_text() == "hello world\n"  # unchanged


def test_apply_edit_whitespace_match(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("    indented line\n    another line\n")
    # Search without leading whitespace should still match via strategy 2
    assert apply_edit(str(f), "indented line\nanother line", "replaced\nlines") is True
    content = f.read_text()
    assert "replaced" in content


# -- apply_all_edits ----------------------------------------------------------

def test_apply_all_edits_success(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("aaa\nbbb\nccc\n")
    edits = [
        {"file_path": str(f), "search": "bbb", "replace": "BBB"},
    ]
    result = apply_all_edits(edits)
    assert result["applied"] == 1
    assert result["failed"] == 0
    assert f.read_text() == "aaa\nBBB\nccc\n"


def test_apply_all_edits_missing_file_path():
    edits = [{"file_path": None, "search": "x", "replace": "y"}]
    result = apply_all_edits(edits)
    assert result["failed"] == 1
    assert "No file_path" in result["errors"][0]


def test_apply_all_edits_file_not_found():
    edits = [{"file_path": "/nonexistent/f.py", "search": "x", "replace": "y"}]
    result = apply_all_edits(edits)
    assert result["failed"] == 1
    assert "File not found" in result["errors"][0]


def test_apply_all_edits_empty_search(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("content\n")
    edits = [{"file_path": str(f), "search": "", "replace": "y"}]
    result = apply_all_edits(edits)
    assert result["failed"] == 1
    assert "Empty search" in result["errors"][0]
