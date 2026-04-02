import os
import subprocess
from datetime import datetime


async def prime(path: str = ".") -> str:
    """Load full project context: MANTIS.md, git history, file tree, status."""
    parts = []

    # Read MANTIS.md
    mantis_path = os.path.join(path, "MANTIS.md")
    if os.path.exists(mantis_path):
        with open(mantis_path, "r") as f:
            parts.append(f"## MANTIS.md\n{f.read()}\n")

    # Recent git commits
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-10"],
            capture_output=True, text=True, check=True, cwd=path,
        )
        if result.stdout.strip():
            parts.append(f"## Recent Commits\n{result.stdout.strip()}\n")
    except Exception:
        parts.append("## Recent Commits\nGit not available\n")

    # File structure (depth 2)
    tree_lines = []
    for root, dirs, files in os.walk(path):
        depth = root.replace(path, "").count(os.sep)
        if depth > 2:
            dirs.clear()
            continue
        indent = "  " * depth
        tree_lines.append(f"{indent}{os.path.basename(root)}/")
        for f in sorted(files)[:20]:
            tree_lines.append(f"{indent}  {f}")
    parts.append(f"## File Structure\n" + "\n".join(tree_lines[:60]) + "\n")

    # Status
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts.append(
        f"## Status\n"
        f"- Time: {now}\n"
        f"- MANTIS.md: {'found' if os.path.exists(mantis_path) else 'not found'}\n"
        f"- Git: {'yes' if os.path.exists(os.path.join(path, '.git')) else 'no'}\n"
        f"- CWD: {os.path.abspath(path)}\n"
    )

    return "\n".join(parts)


def register_prime(registry) -> None:
    """Register the prime skill with a ToolRegistry."""
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project root path", "default": "."}
        },
        "required": [],
    }
    registry.register("prime", "Load full project context on startup", schema, prime)
