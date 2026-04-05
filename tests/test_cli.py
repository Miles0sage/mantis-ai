from __future__ import annotations

from argparse import Namespace
from unittest.mock import MagicMock, patch

from mantis.cli import (
    build_config_from_args,
    build_issue_pr_prompt,
    build_pr_review_bundle,
    create_draft_pr_with_gh,
    cmd_issue_worktree,
    cmd_issue_pr,
    create_parser,
    fetch_issue_from_gh,
)


def test_build_issue_pr_prompt_contains_workflow():
    prompt = build_issue_pr_prompt(
        title="Add billing retry flow",
        body="Need retry handling and tests.",
        issue_number=42,
        repo_name="acme/api",
    )
    assert "[ISSUE TO PR WORKFLOW]" in prompt
    assert "Issue: #42" in prompt
    assert "acme/api" in prompt
    assert "Plan the work as explicit bounded tasks" in prompt
    assert "Draft a PR-ready summary" in prompt


def test_issue_pr_parser_accepts_core_args():
    parser = create_parser()
    args = parser.parse_args(
        [
            "issue-pr",
            "--title",
            "Add retry flow",
            "--body",
            "Need tests too",
            "--issue-number",
            "12",
            "--repo-name",
            "acme/api",
            "--dry-run",
        ]
    )
    assert args.command == "issue-pr"
    assert args.title == "Add retry flow"
    assert args.body == "Need tests too"
    assert args.issue_number == 12
    assert args.repo_name == "acme/api"
    assert args.dry_run is True


def test_issue_pr_parser_accepts_from_gh():
    parser = create_parser()
    args = parser.parse_args(
        [
            "issue-pr",
            "--from-gh",
            "--issue-number",
            "12",
            "--repo-name",
            "acme/api",
        ]
    )
    assert args.from_gh is True
    assert args.issue_number == 12
    assert args.repo_name == "acme/api"


def test_issue_pr_parser_accepts_use_worktree():
    parser = create_parser()
    args = parser.parse_args(
        [
            "issue-pr",
            "--title",
            "Add retry flow",
            "--use-worktree",
            "--worktree-root-dir",
            "/tmp/wt",
        ]
    )
    assert args.use_worktree is True
    assert args.worktree_root_dir == "/tmp/wt"


def test_issue_pr_parser_accepts_create_draft_pr():
    parser = create_parser()
    args = parser.parse_args(
        [
            "issue-pr",
            "--title",
            "Add retry flow",
            "--create-draft-pr",
        ]
    )
    assert args.create_draft_pr is True


def test_issue_worktree_parser_accepts_args():
    parser = create_parser()
    args = parser.parse_args(
        [
            "issue-worktree",
            "--repo-dir",
            "/tmp/repo",
            "--title",
            "Add retry flow",
            "--issue-number",
            "12",
            "--base-branch",
            "main",
        ]
    )
    assert args.command == "issue-worktree"
    assert args.repo_dir == "/tmp/repo"
    assert args.title == "Add retry flow"
    assert args.issue_number == 12
    assert args.base_branch == "main"


def test_cmd_issue_pr_dry_run_prints_prompt():
    app = MagicMock()
    with patch("mantis.cli.print_response") as mock_print:
        code = cmd_issue_pr(
            app,
            title="Add retry flow",
            body="Need tests too",
            issue_number=12,
            repo_name="acme/api",
            dry_run=True,
        )
    assert code == 0
    assert mock_print.called
    printed = mock_print.call_args.args[0]
    assert "Issue: #12" in printed
    assert "Draft a PR-ready summary" in printed


def test_cmd_issue_pr_runs_prompt_directly():
    app = MagicMock()
    app.run.return_value = "Implemented retry handling."
    app.last_stats = {"execution": {"tasks": [], "verifier": {"verdict": "pass", "reason": "ok"}}}
    with patch("mantis.cli.print_response"), patch("mantis.cli.print_system"):
        code = cmd_issue_pr(
            app,
            title="Add retry flow",
            body="Need tests too",
            issue_number=12,
            repo_name="acme/api",
            dry_run=False,
        )
    assert code == 0
    assert app.run.called
    prompt = app.run.call_args.args[0]
    assert "Issue: #12" in prompt
    assert "Return work in a way that could be turned into a pull request" in prompt


def test_build_pr_review_bundle_uses_execution_tasks_and_verification():
    bundle = build_pr_review_bundle(
        "Add retry flow",
        "Implemented retry handling.",
        stats={
            "execution": {
                "tasks": [
                    {"file_targets": ["app/service.py", "tests/test_service.py"]},
                ],
                "verifier": {"verdict": "pass", "reason": "tests and postconditions passed"},
            }
        },
        issue_number=12,
        git_review={
            "branch": "mantis/issue-12-add-retry-flow",
            "path": "/tmp/wt",
            "changed_files": ["app/service.py", "tests/test_service.py"],
            "diff": "diff --git a/app/service.py b/app/service.py",
        },
    )
    assert "PR title: [Issue #12] Add retry flow" in bundle
    assert "- app/service.py" in bundle
    assert "- tests/test_service.py" in bundle
    assert "- verdict: pass" in bundle
    assert "tests and postconditions passed" in bundle
    assert "- branch: mantis/issue-12-add-retry-flow" in bundle
    assert "Diff preview:" in bundle


def test_build_config_from_args_includes_command():
    args = Namespace(
        model=None,
        base_url=None,
        api_key=None,
        complexity=None,
        max_iterations=25,
        command="issue-pr",
    )
    config = build_config_from_args(args)
    assert config["command"] == "issue-pr"


def test_fetch_issue_from_gh_parses_json():
    completed = MagicMock()
    completed.stdout = '{"title":"Add retry flow","body":"Need retry handling and tests."}'
    with patch("mantis.cli.subprocess.run", return_value=completed) as mock_run:
        title, body = fetch_issue_from_gh(12, "acme/api")
    assert title == "Add retry flow"
    assert "retry handling" in body
    cmd = mock_run.call_args.args[0]
    assert "--repo" in cmd
    assert "acme/api" in cmd


def test_fetch_issue_from_gh_raises_on_bad_json():
    completed = MagicMock()
    completed.stdout = "not-json"
    with patch("mantis.cli.subprocess.run", return_value=completed):
        try:
            fetch_issue_from_gh(12)
        except RuntimeError as e:
            assert "invalid JSON" in str(e)
        else:
            raise AssertionError("Expected RuntimeError for invalid JSON")


def test_create_draft_pr_with_gh_builds_command():
    completed = MagicMock()
    completed.stdout = "https://github.com/acme/api/pull/12\n"
    with patch("mantis.cli.subprocess.run", return_value=completed) as mock_run:
        result = create_draft_pr_with_gh(
            title="[Issue #12] Add retry flow",
            body="summary",
            branch="mantis/issue-12-add-retry-flow",
            repo_name="acme/api",
        )
    assert "pull/12" in result
    cmd = mock_run.call_args.args[0]
    assert cmd[:4] == ["gh", "pr", "create", "--draft"]
    assert "--head" in cmd
    assert "mantis/issue-12-add-retry-flow" in cmd


def test_cmd_issue_pr_prints_pr_review_bundle():
    app = MagicMock()
    app.run.return_value = "Implemented retry handling."
    app.last_stats = {
        "execution": {
            "tasks": [{"file_targets": ["app/service.py"]}],
            "verifier": {"verdict": "pass", "reason": "tests passed"},
        }
    }
    with patch("mantis.cli.print_response") as mock_print, patch("mantis.cli.print_system"):
        code = cmd_issue_pr(
            app,
            title="Add retry flow",
            body="Need tests too",
            issue_number=12,
            repo_name="acme/api",
            dry_run=False,
        )
    assert code == 0
    printed_values = [call.args[0] for call in mock_print.call_args_list]
    assert any("[PR REVIEW BUNDLE]" in value for value in printed_values)
    assert any("app/service.py" in value for value in printed_values)


def test_cmd_issue_pr_use_worktree_runs_in_isolated_app():
    base_app = MagicMock()
    base_app.project_dir = "/tmp/repo"
    base_app.config = {"model": "gpt-4o-mini"}

    isolated_app = MagicMock()
    isolated_app.project_dir = "/tmp/worktrees/issue-12-add-retry-flow"
    isolated_app.run.return_value = "Implemented retry handling."
    isolated_app.last_stats = {
        "execution": {
            "tasks": [{"file_targets": ["app/service.py"]}],
            "verifier": {"verdict": "pass", "reason": "tests passed"},
        }
    }

    with patch("mantis.cli.create_issue_worktree", return_value={
        "repo_dir": "/tmp/repo",
        "worktree_dir": "/tmp/worktrees/issue-12-add-retry-flow",
        "branch": "mantis/issue-12-add-retry-flow",
        "base_branch": "HEAD",
    }), patch("mantis.cli.collect_git_review", return_value={
        "branch": "mantis/issue-12-add-retry-flow",
        "path": "/tmp/worktrees/issue-12-add-retry-flow",
        "changed_files": ["app/service.py"],
        "diff": "diff --git a/app/service.py b/app/service.py",
    }), patch("mantis.cli.MantisApp", return_value=isolated_app), patch("mantis.cli.print_response") as mock_print, patch("mantis.cli.print_system"):
        code = cmd_issue_pr(
            base_app,
            title="Add retry flow",
            body="Need tests too",
            issue_number=12,
            repo_name="acme/api",
            dry_run=False,
            use_worktree=True,
            worktree_root_dir="/tmp/worktrees",
        )
    assert code == 0
    assert isolated_app.run.called
    printed_values = [call.args[0] for call in mock_print.call_args_list]
    assert any("[WORKTREE]" in value for value in printed_values)
    assert isolated_app.last_stats["execution"]["pr_review"]["changed_files"] == ["app/service.py"]


def test_cmd_issue_pr_can_create_draft_pr():
    app = MagicMock()
    app.project_dir = "/tmp/repo"
    app.config = {"model": "gpt-4o-mini"}
    app.run.return_value = "Implemented retry handling."
    app.last_stats = {
        "execution": {
            "tasks": [{"file_targets": ["app/service.py"]}],
            "verifier": {"verdict": "pass", "reason": "tests passed"},
        }
    }
    with patch("mantis.cli.collect_git_review", return_value={
        "branch": "mantis/issue-12-add-retry-flow",
        "path": "/tmp/repo",
        "changed_files": ["app/service.py"],
        "diff": "diff --git a/app/service.py b/app/service.py",
    }), patch("mantis.cli.create_draft_pr_with_gh", return_value="https://github.com/acme/api/pull/12") as mock_pr, patch("mantis.cli.print_response") as mock_print, patch("mantis.cli.print_system"):
        code = cmd_issue_pr(
            app,
            title="Add retry flow",
            body="Need tests too",
            issue_number=12,
            repo_name="acme/api",
            dry_run=False,
            create_draft_pr=True,
        )
    assert code == 0
    assert mock_pr.called
    printed_values = [call.args[0] for call in mock_print.call_args_list]
    assert any("[DRAFT PR]" in value for value in printed_values)


def test_cmd_issue_worktree_prints_created_worktree():
    with patch("mantis.cli.create_issue_worktree", return_value={
        "repo_dir": "/tmp/repo",
        "worktree_dir": "/tmp/worktrees/issue-12-add-retry-flow",
        "branch": "mantis/issue-12-add-retry-flow",
        "base_branch": "main",
    }), patch("mantis.cli.print_response") as mock_print:
        code = cmd_issue_worktree(
            repo_dir="/tmp/repo",
            title="Add retry flow",
            issue_number=12,
            base_branch="main",
        )
    assert code == 0
    printed = mock_print.call_args.args[0]
    assert "[ISSUE WORKTREE]" in printed
    assert "mantis/issue-12-add-retry-flow" in printed
