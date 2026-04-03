from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from mantis.tools.edit_applicator import preview_apply_edit


def _unified_diff(path: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def build_tool_preview(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "write_file":
        path = tool_input["file_path"]
        before = Path(path).read_text(encoding="utf-8") if Path(path).exists() else ""
        after = tool_input["content"]
        return {"kind": "diff", "path": path, "diff": _unified_diff(path, before, after)}

    if tool_name == "edit_file":
        path = tool_input["file_path"]
        file_path = Path(path)
        before = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
        after = before.replace(tool_input["old_string"], tool_input["new_string"])
        return {"kind": "diff", "path": path, "diff": _unified_diff(path, before, after)}

    if tool_name == "apply_edit":
        path = tool_input["file_path"]
        preview = preview_apply_edit(path, tool_input["search_text"], tool_input["replace_text"])
        if preview is None:
            return {
                "kind": "message",
                "path": path,
                "message": "Could not build preview: search text did not match current file.",
            }
        before, after = preview
        return {"kind": "diff", "path": path, "diff": _unified_diff(path, before, after)}

    if tool_name == "run_bash":
        return {
            "kind": "command",
            "command": tool_input.get("command", ""),
            "timeout": tool_input.get("timeout", 120),
        }

    return {"kind": "message", "message": "No preview available."}
