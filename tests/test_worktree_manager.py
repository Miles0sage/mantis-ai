from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from mantis.core.worktree_manager import (
    build_worktree_names,
    collect_git_review,
    create_issue_worktree,
    map_repo_path_to_worktree,
    rewrite_prompt_paths_for_worktree,
)


def test_build_worktree_names_with_issue_number():
    branch, dirname = build_worktree_names(42, "Add Retry Flow")
    assert branch == "mantis/issue-42-add-retry-flow"
    assert dirname == "issue-42-add-retry-flow"


def test_build_worktree_names_without_issue_number():
    branch, dirname = build_worktree_names(None, "Add Retry Flow")
    assert branch.startswith("mantis/task-add-retry-flow-")
    assert dirname.startswith("task-add-retry-flow-")


def test_create_issue_worktree_builds_git_command(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    root_dir = tmp_path / "worktrees"
    with patch("mantis.core.worktree_manager.subprocess.run", return_value=MagicMock()) as mock_run:
        result = create_issue_worktree(
            repo_dir=str(repo_dir),
            title="Add Retry Flow",
            issue_number=42,
            base_branch="main",
            root_dir=str(root_dir),
        )
    cmd = mock_run.call_args.args[0]
    assert cmd[:5] == ["git", "-C", str(repo_dir.resolve()), "worktree", "add"]
    assert result["branch"] == "mantis/issue-42-add-retry-flow"
    assert Path(result["worktree_dir"]).name == "issue-42-add-retry-flow"


def test_collect_git_review_parses_branch_status_and_diff(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    runs = [
        MagicMock(stdout="mantis/issue-42-add-retry-flow\n"),
        MagicMock(stdout=" M app/service.py\n?? tests/test_service.py\n"),
        MagicMock(stdout="diff --git a/app/service.py b/app/service.py\n+new line\n"),
    ]
    with patch("mantis.core.worktree_manager.subprocess.run", side_effect=runs):
        review = collect_git_review(str(repo_dir))
    assert review["branch"] == "mantis/issue-42-add-retry-flow"
    assert review["changed_files"] == ["app/service.py", "tests/test_service.py"]
    assert "diff --git" in review["diff"]


def test_map_repo_path_to_worktree_handles_relative_and_absolute_paths(tmp_path):
    repo_dir = tmp_path / "repo"
    worktree_dir = tmp_path / "wt"
    repo_dir.mkdir()
    worktree_dir.mkdir()

    relative = map_repo_path_to_worktree(str(repo_dir), str(worktree_dir), "app/service.py")
    absolute = map_repo_path_to_worktree(
        str(repo_dir),
        str(worktree_dir),
        str(repo_dir / "tests" / "test_service.py"),
    )

    assert relative == str(worktree_dir / "app" / "service.py")
    assert absolute == str(worktree_dir / "tests" / "test_service.py")


def test_rewrite_prompt_paths_for_worktree_updates_prompt_and_targets(tmp_path):
    repo_dir = tmp_path / "repo"
    worktree_dir = tmp_path / "wt"
    repo_dir.mkdir()
    worktree_dir.mkdir()

    prompt, targets = rewrite_prompt_paths_for_worktree(
        "Fix app/service.py and keep tests/test_service.py passing.",
        str(repo_dir),
        str(worktree_dir),
        ["app/service.py", "tests/test_service.py"],
    )

    assert str(worktree_dir / "app" / "service.py") in prompt
    assert str(worktree_dir / "tests" / "test_service.py") in prompt
    assert targets == [
        str(worktree_dir / "app" / "service.py"),
        str(worktree_dir / "tests" / "test_service.py"),
    ]
