from __future__ import annotations

from mantis.core.diff_preview import build_tool_preview


def test_build_tool_preview_for_write_file(tmp_path):
    path = tmp_path / "demo.py"
    preview = build_tool_preview(
        "write_file",
        {"file_path": str(path), "content": "print('hi')\n"},
    )

    assert preview["kind"] == "diff"
    assert "--- a/" in preview["diff"]
    assert "print('hi')" in preview["diff"]


def test_build_tool_preview_for_edit_file(tmp_path):
    path = tmp_path / "demo.py"
    path.write_text("hello world\n", encoding="utf-8")
    preview = build_tool_preview(
        "edit_file",
        {
            "file_path": str(path),
            "old_string": "hello",
            "new_string": "goodbye",
        },
    )

    assert preview["kind"] == "diff"
    assert "-hello world" in preview["diff"]
    assert "+goodbye world" in preview["diff"]
