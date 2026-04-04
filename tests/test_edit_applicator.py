from pathlib import Path

from mantis.tools.edit_applicator import apply_all_edits, apply_edit, parse_search_replace


def test_parse_search_replace_extracts_file_path_and_blocks():
    text = (
        "app.py\n"
        "<<<<<<< SEARCH\nold = 1\n=======\nold = 2\n>>>>>>> REPLACE\n"
    )
    edits = parse_search_replace(text)
    assert edits == [{"file_path": "app.py", "search": "old = 1", "replace": "old = 2"}]


def test_apply_edit_replaces_exact_match(tmp_path):
    path = tmp_path / "app.py"
    path.write_text("old = 1\n")
    assert apply_edit(str(path), "old = 1", "old = 2")
    assert path.read_text() == "old = 2\n"


def test_apply_all_edits_reports_failures(tmp_path):
    path = tmp_path / "app.py"
    path.write_text("value = 1\n")
    result = apply_all_edits(
        [
            {"file_path": str(path), "search": "value = 1", "replace": "value = 2"},
            {"file_path": str(path), "search": "missing", "replace": "noop"},
        ]
    )
    assert result["applied"] == 1
    assert result["failed"] == 1
    assert result["errors"]
