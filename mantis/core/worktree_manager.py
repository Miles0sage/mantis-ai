from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path


def _slugify(text: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in text).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned[:48] or "task"


def build_worktree_names(issue_number: int | None, title: str) -> tuple[str, str]:
    """Return (branch_name, dir_name) for an issue-driven isolated worktree."""
    slug = _slugify(title)
    if issue_number is not None:
        branch = f"mantis/issue-{issue_number}-{slug}"
        dirname = f"issue-{issue_number}-{slug}"
    else:
        digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
        branch = f"mantis/task-{slug}-{digest}"
        dirname = f"task-{slug}-{digest}"
    return branch, dirname


def create_issue_worktree(
    repo_dir: str,
    title: str,
    issue_number: int | None = None,
    base_branch: str = "HEAD",
    root_dir: str | None = None,
) -> dict[str, str]:
    """Create a git worktree for an issue-driven task."""
    repo_path = Path(repo_dir).resolve()
    branch_name, dir_name = build_worktree_names(issue_number, title)
    target_root = Path(root_dir).resolve() if root_dir else repo_path.parent / ".mantis-worktrees"
    target_root.mkdir(parents=True, exist_ok=True)
    worktree_path = target_root / dir_name

    if worktree_path.exists():
        return {
            "repo_dir": str(repo_path),
            "worktree_dir": str(worktree_path),
            "branch": branch_name,
            "base_branch": base_branch,
        }

    cmd = [
        "git",
        "-C",
        str(repo_path),
        "worktree",
        "add",
        "-b",
        branch_name,
        str(worktree_path),
        base_branch,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise RuntimeError("git is not installed or not on PATH") from e
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        raise RuntimeError(f"git worktree add failed: {stderr or e}") from e

    return {
        "repo_dir": str(repo_path),
        "worktree_dir": str(worktree_path),
        "branch": branch_name,
        "base_branch": base_branch,
    }


def collect_git_review(repo_dir: str, diff_limit: int = 4000) -> dict[str, object]:
    """Collect branch, changed files, and a diff preview for a repo/worktree."""
    repo_path = Path(repo_dir).resolve()

    def _run(args: list[str]) -> str:
        try:
            proc = subprocess.run(
                ["git", "-C", str(repo_path), *args],
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as e:
            raise RuntimeError("git is not installed or not on PATH") from e
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "").strip()
            raise RuntimeError(f"git {' '.join(args)} failed: {stderr or e}") from e
        return proc.stdout.strip()

    branch = _run(["branch", "--show-current"]) or "HEAD"
    changed = _run(["status", "--short"])
    changed_files: list[str] = []
    for line in changed.splitlines():
        if not line.strip():
            continue
        raw_path = line[2:].strip() if len(line) > 2 else line.strip()
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", 1)[1].strip()
        changed_files.append(raw_path)

    diff = _run(["diff", "--", "."])
    if len(diff) > diff_limit:
        diff = diff[:diff_limit] + "\n... [diff truncated]"

    return {
        "branch": branch,
        "changed_files": changed_files,
        "diff": diff,
        "path": str(repo_path),
    }


def is_git_repo(repo_dir: str) -> bool:
    repo_path = Path(repo_dir).resolve()
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--is-inside-work-tree"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
    return proc.stdout.strip() == "true"


def map_repo_path_to_worktree(
    repo_dir: str,
    worktree_dir: str,
    target_path: str,
) -> str:
    repo_path = Path(repo_dir).resolve()
    worktree_path = Path(worktree_dir).resolve()
    raw_target = Path(target_path)
    if not raw_target.is_absolute():
        return str((worktree_path / raw_target).resolve())

    resolved = raw_target.resolve()
    try:
        relative = resolved.relative_to(repo_path)
    except ValueError:
        return str(resolved)
    return str((worktree_path / relative).resolve())


def rewrite_prompt_paths_for_worktree(
    prompt: str,
    repo_dir: str,
    worktree_dir: str,
    file_targets: list[str],
) -> tuple[str, list[str]]:
    rewritten_targets: list[str] = []
    replacements: dict[str, str] = {}
    for target in file_targets:
        rewritten = map_repo_path_to_worktree(repo_dir, worktree_dir, target)
        rewritten_targets.append(rewritten)
        replacements[target] = rewritten
        if not os.path.isabs(target):
            replacements[str((Path(repo_dir) / target).resolve())] = rewritten

    rewritten_prompt = prompt
    for source, dest in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        rewritten_prompt = rewritten_prompt.replace(source, dest)

    return rewritten_prompt, rewritten_targets
