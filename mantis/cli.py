# mantis/cli.py
import argparse
import os
import subprocess
import sys
import signal
from typing import Dict, Any, Optional

from mantis.app import MantisApp
from mantis.core.worktree_manager import collect_git_review, create_issue_worktree


# ANSI color codes
class Colors:
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def colorize(text: str, color: str) -> str:
    """Apply ANSI color to text."""
    return f"{color}{text}{Colors.RESET}"


def print_error(message: str) -> None:
    """Print error message in red."""
    print(colorize(f"Error: {message}", Colors.RED), file=sys.stderr)


def print_system(message: str) -> None:
    """Print system message in yellow."""
    print(colorize(f"[system] {message}", Colors.YELLOW))


def print_response(message: str) -> None:
    """Print assistant response in green."""
    print(colorize(message, Colors.GREEN))


def print_token_stats(stats: Dict[str, Any]) -> None:
    """Print token usage statistics in dim color."""
    if not stats:
        return
    
    parts = []
    if "input_tokens" in stats:
        parts.append(f"input: {stats['input_tokens']}")
    if "output_tokens" in stats:
        parts.append(f"output: {stats['output_tokens']}")
    if "total_tokens" in stats:
        parts.append(f"total: {stats['total_tokens']}")
    if "cost" in stats:
        parts.append(f"cost: ${stats['cost']:.4f}")
    
    if parts:
        print(colorize(f"[tokens: {', '.join(parts)}]", Colors.DIM))


def print_model_info(models: Dict[str, Dict[str, Any]]) -> None:
    """Print registered models with intelligence scores and costs."""
    print(colorize("\nRegistered Models:", Colors.BOLD + Colors.YELLOW))
    print("-" * 70)
    
    header = f"{'Model':<30} {'Provider':<15} {'Intelligence':<12} {'Cost/1K tokens':<15}"
    print(colorize(header, Colors.BOLD))
    print("-" * 70)
    
    for name, info in models.items():
        provider = info.get("provider", "unknown")
        intelligence = info.get("intelligence_score", info.get("intelligence", 0))
        cost = info.get("cost_per_1k_tokens", info.get("cost", 0))
        
        # Color code intelligence
        if intelligence >= 8:
            intel_color = Colors.GREEN
        elif intelligence >= 5:
            intel_color = Colors.YELLOW
        else:
            intel_color = Colors.DIM
        
        row = f"{name:<30} {provider:<15} {colorize(f'{intelligence}/10', intel_color):<12} ${cost:<14.4f}"
        print(row)
    
    print("-" * 70)
    print()


def print_tools_info(tools: Dict[str, Dict[str, Any]]) -> None:
    """Print registered tools."""
    print(colorize("\nRegistered Tools:", Colors.BOLD + Colors.YELLOW))
    print("-" * 70)
    
    if not tools:
        print(colorize("No tools registered.", Colors.DIM))
        return
    
    header = f"{'Tool Name':<25} {'Description':<40}"
    print(colorize(header, Colors.BOLD))
    print("-" * 70)
    
    for name, info in tools.items():
        description = info.get("description", "No description")[:38]
        print(f"{name:<25} {description:<40}")
    
    print("-" * 70)
    print()


def build_issue_pr_prompt(
    title: str,
    body: str,
    issue_number: int | None = None,
    repo_name: str | None = None,
) -> str:
    """Build a structured issue -> plan -> implement -> verify -> PR prompt."""
    issue_ref = f"#{issue_number}" if issue_number is not None else "(untracked)"
    repo_label = repo_name or "current repository"
    cleaned_body = body.strip() or "No additional issue details provided."
    return (
        "[ISSUE TO PR WORKFLOW]\n"
        f"Repository: {repo_label}\n"
        f"Issue: {issue_ref}\n"
        f"Title: {title.strip()}\n\n"
        "Issue body:\n"
        f"{cleaned_body}\n\n"
        "Required workflow:\n"
        "1. Plan the work as explicit bounded tasks.\n"
        "2. Inspect the repository semantically when possible before editing.\n"
        "3. Implement the smallest correct change set.\n"
        "4. Verify with compile/tests and required postconditions.\n"
        "5. Summarize the exact files changed and the verification result.\n"
        "6. Draft a PR-ready summary with title, change summary, risks, and verification notes.\n\n"
        "Return work in a way that could be turned into a pull request, not just a chat reply."
    )


def build_pr_review_bundle(
    issue_title: str,
    response: str,
    stats: Dict[str, Any] | None = None,
    issue_number: int | None = None,
    git_review: Dict[str, Any] | None = None,
) -> str:
    """Build a PR-ready summary from the latest execution state."""
    stats = stats or {}
    execution = stats.get("execution") or {}
    tasks = execution.get("tasks") or []
    verification = execution.get("verifier") or stats.get("verification") or {}

    changed_files: list[str] = []
    for path in (git_review or {}).get("changed_files") or []:
        if path not in changed_files:
            changed_files.append(path)
    if not changed_files:
        for task in tasks:
            for file_target in task.get("file_targets") or []:
                if file_target not in changed_files:
                    changed_files.append(file_target)

    title_prefix = f"[Issue #{issue_number}] " if issue_number is not None else ""
    pr_title = f"{title_prefix}{issue_title.strip()}"

    lines = [
        "[PR REVIEW BUNDLE]",
        f"PR title: {pr_title}",
        "",
        "Change summary:",
        response.strip() or "(no response)",
        "",
        "Changed files:",
    ]
    if changed_files:
        lines.extend(f"- {path}" for path in changed_files)
    else:
        lines.append("- (no tracked file targets)")

    lines.extend(["", "Verification:"])
    verdict = verification.get("verdict")
    reason = verification.get("reason")
    if verdict:
        lines.append(f"- verdict: {verdict}")
    if reason:
        lines.append(f"- reason: {reason}")
    if not verdict and not reason:
        lines.append("- no verifier summary recorded")

    if git_review:
        lines.extend(["", "Git review:"])
        if git_review.get("branch"):
            lines.append(f"- branch: {git_review['branch']}")
        if git_review.get("path"):
            lines.append(f"- path: {git_review['path']}")
        diff = git_review.get("diff")
        if diff:
            lines.extend(["", "Diff preview:", str(diff)])

    lines.extend(
        [
            "",
            "Risks:",
            "- review changed files before merge",
            "- confirm verification matches intended behavior, not only prompt compliance",
        ]
    )
    return "\n".join(lines)


def fetch_issue_from_gh(issue_number: int, repo_name: str | None = None) -> tuple[str, str]:
    """Fetch a GitHub issue title/body using the gh CLI."""
    cmd = ["gh", "issue", "view", str(issue_number), "--json", "title,body"]
    if repo_name:
        cmd.extend(["--repo", repo_name])
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as e:
        raise RuntimeError("gh CLI is not installed or not on PATH") from e
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        raise RuntimeError(f"gh issue view failed: {stderr or e}") from e

    import json

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError("gh issue view returned invalid JSON") from e

    title = (payload.get("title") or "").strip()
    body = payload.get("body") or ""
    if not title:
        raise RuntimeError("gh issue view returned an empty title")
    return title, body


def create_draft_pr_with_gh(
    title: str,
    body: str,
    branch: str,
    repo_name: str | None = None,
) -> str:
    """Create a draft PR with gh and return the resulting URL or stdout."""
    cmd = ["gh", "pr", "create", "--draft", "--title", title, "--body", body, "--head", branch]
    if repo_name:
        cmd.extend(["--repo", repo_name])
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as e:
        raise RuntimeError("gh CLI is not installed or not on PATH") from e
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        raise RuntimeError(f"gh pr create failed: {stderr or e}") from e
    return (proc.stdout or "").strip()


def build_config_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    """Build configuration dict from parsed args and environment variables."""
    config: Dict[str, Any] = {}
    
    # Model settings
    if args.model:
        config["model"] = args.model
    
    if args.base_url:
        config["base_url"] = args.base_url
    
    # API key: args takes precedence over env var
    api_key = args.api_key or os.environ.get("MANTIS_API_KEY")
    if api_key:
        config["api_key"] = api_key
    
    # Complexity/routing
    if args.complexity:
        config["complexity"] = args.complexity
    
    # Max iterations
    config["max_iterations"] = args.max_iterations
    
    # Command-specific settings
    if hasattr(args, 'command'):
        config["command"] = args.command
    
    return config


def cmd_chat(app: MantisApp) -> int:
    """Interactive REPL chat mode."""
    print_system("Starting interactive chat. Type 'exit' or Ctrl+C to quit.")
    print_system("Press Enter on empty line to send your message.\n")
    
    lines = []
    
    while True:
        try:
            try:
                user_input = input(colorize("\nYou: ", Colors.BOLD))
            except (EOFError, KeyboardInterrupt):
                print()
                break
            
            if user_input.lower() in ("exit", "quit", "q"):
                print_system("Goodbye!")
                break
            
            if not user_input.strip():
                if lines:
                    # Empty line sends accumulated message
                    full_prompt = "\n".join(lines)
                    lines = []
                    
                    print()
                    print_response("Mantis: ")
                    
                    try:
                        for chunk in app.stream_chat(full_prompt):
                            print(chunk, end="", flush=True)
                        print()  # Newline after response
                    except Exception as e:
                        print_error(str(e))
                continue
            
            lines.append(user_input)
            
        except KeyboardInterrupt:
            print()
            print_system("Use 'exit' or Ctrl+C to quit.")
            continue
    
    return 0


def cmd_run(app: MantisApp, prompt: str) -> int:
    """Run a single prompt."""
    print_system(f"Running prompt: {prompt[:50]}{'...' if len(prompt) > 50 else ''}")
    print()
    
    try:
        response = app.run(prompt)
        
        print_response("Mantis: ")
        print_response(response)
        print()
        
        # Print token stats if available
        if hasattr(app, 'last_stats') and app.last_stats:
            print_token_stats(app.last_stats)
        
        return 0
        
    except Exception as e:
        print_error(str(e))
        return 1


def cmd_issue_pr(
    app: MantisApp,
    title: str,
    body: str,
    issue_number: int | None = None,
    repo_name: str | None = None,
    dry_run: bool = False,
    use_worktree: bool = False,
    worktree_root_dir: str | None = None,
    create_draft_pr: bool = False,
) -> int:
    """Run the issue -> PR workflow prompt or print it for inspection."""
    prompt = build_issue_pr_prompt(
        title=title,
        body=body,
        issue_number=issue_number,
        repo_name=repo_name,
    )
    if dry_run:
        print_response(prompt)
        return 0
    execution_app = app
    worktree: dict[str, str] | None = None
    if use_worktree:
        try:
            worktree = create_issue_worktree(
                repo_dir=app.project_dir,
                title=title,
                issue_number=issue_number,
                root_dir=worktree_root_dir,
            )
        except RuntimeError as e:
            print_error(str(e))
            return 1
        print_system(
            f"Using isolated worktree {worktree['worktree_dir']} on branch {worktree['branch']}"
        )
        execution_app = MantisApp(app.config, project_dir=worktree["worktree_dir"])
    print_system(f"Running issue -> PR workflow for: {title}")
    print()
    try:
        response = execution_app.run(prompt)
        try:
            git_review = collect_git_review(execution_app.project_dir)
        except RuntimeError:
            git_review = {
                "branch": None,
                "path": execution_app.project_dir,
                "changed_files": [],
                "diff": "",
            }
        execution = execution_app.last_stats.setdefault("execution", {})
        execution["worktree"] = {
            "branch": git_review.get("branch"),
            "path": git_review.get("path"),
        }
        execution["pr_review"] = {
            "title": f"[Issue #{issue_number}] {title}" if issue_number is not None else title,
            "changed_files": git_review.get("changed_files", []),
            "verdict": (execution.get("verifier") or {}).get("verdict"),
            "reason": (execution.get("verifier") or {}).get("reason"),
            "diff_preview": git_review.get("diff"),
        }
        print_response("Mantis: ")
        print_response(response)
        print()
        print_response(
            build_pr_review_bundle(
                title,
                response,
                execution_app.last_stats,
                issue_number,
                git_review=git_review,
            )
        )
        print()
        if use_worktree:
            print_response(
                f"[WORKTREE] {execution_app.project_dir}"
            )
            print()
        if create_draft_pr:
            branch = (git_review or {}).get("branch")
            if not branch:
                print_error("Cannot create draft PR without a git branch name")
                return 1
            pr_body = build_pr_review_bundle(
                title,
                response,
                execution_app.last_stats,
                issue_number,
                git_review=git_review,
            )
            pr_title = f"[Issue #{issue_number}] {title}" if issue_number is not None else title
            pr_result = create_draft_pr_with_gh(pr_title, pr_body, branch, repo_name)
            if pr_result:
                print_response(f"[DRAFT PR] {pr_result}")
                print()
        if hasattr(execution_app, "last_stats") and execution_app.last_stats:
            print_token_stats(execution_app.last_stats)
        return 0
    except Exception as e:
        print_error(str(e))
        return 1


def cmd_issue_worktree(
    repo_dir: str,
    title: str,
    issue_number: int | None = None,
    base_branch: str = "HEAD",
    root_dir: str | None = None,
) -> int:
    """Create an isolated git worktree for an issue-driven task."""
    try:
        result = create_issue_worktree(
            repo_dir=repo_dir,
            title=title,
            issue_number=issue_number,
            base_branch=base_branch,
            root_dir=root_dir,
        )
    except RuntimeError as e:
        print_error(str(e))
        return 1

    lines = [
        "[ISSUE WORKTREE]",
        f"repo: {result['repo_dir']}",
        f"worktree: {result['worktree_dir']}",
        f"branch: {result['branch']}",
        f"base: {result['base_branch']}",
    ]
    print_response("\n".join(lines))
    return 0


def cmd_models(app: MantisApp) -> int:
    """List registered models."""
    try:
        models = app.list_models()
        print_model_info(models)
        return 0
    except Exception as e:
        print_error(str(e))
        return 1


def cmd_tools(app: MantisApp) -> int:
    """List registered tools."""
    try:
        tools = app.list_tools()
        print_tools_info(tools)
        return 0
    except Exception as e:
        print_error(str(e))
        return 1


def cmd_serve(host: str, port: int, open_browser: bool) -> int:
    """Start the MantisAI web dashboard server."""
    import webbrowser
    import threading
    import time

    url = f"http://{host}:{port}"
    print_system(f"Starting MantisAI dashboard at {url}")

    if open_browser:
        def _open():
            time.sleep(1.2)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    try:
        import uvicorn
        from mantis.server import app as server_app
        uvicorn.run(server_app, host=host, port=port, log_level="warning")
    except ImportError:
        print_error("fastapi and uvicorn are required: pip install fastapi uvicorn[standard]")
        return 1
    except Exception as e:
        print_error(str(e))
        return 1

    return 0


def create_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser."""
    parser = argparse.ArgumentParser(
        prog="mantisai",
        description="MantisAI CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  mantisai chat                                Start interactive REPL
  mantisai run "Summarize this repository"     Run single prompt
  mantisai models                              List available models
  mantisai tools                               List available tools
  mantisai run "Analyze this" --model gpt-4o-mini
  mantisai chat --complexity hard
        """
    )
    
    # Global flags
    parser.add_argument(
        "--model", "-m",
        help="Model name to use (overrides config)"
    )
    
    parser.add_argument(
        "--base-url",
        help="API base URL (overrides config)"
    )
    
    parser.add_argument(
        "--api-key",
        help="API key (or set MANTIS_API_KEY env var)"
    )
    
    parser.add_argument(
        "--complexity",
        choices=["simple", "medium", "hard"],
        help="Task complexity for routing"
    )
    
    parser.add_argument(
        "--max-iterations", "-n",
        type=int,
        default=25,
        help="Max agent loop iterations (default: 25)"
    )

    parser.add_argument(
        "--project-dir",
        default=None,
        help="Project directory to look for MANTIS.md (default: current directory)"
    )
    
    # Subcommands
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # chat command
    chat_parser = subparsers.add_parser(
        "chat",
        help="Start interactive REPL chat"
    )
    chat_parser.description = "Start an interactive chat session with Mantis"
    
    # run command
    run_parser = subparsers.add_parser(
        "run",
        help="Run a single prompt"
    )
    run_parser.add_argument(
        "prompt",
        help="The prompt to run"
    )
    
    # models command
    models_parser = subparsers.add_parser(
        "models",
        help="List registered models"
    )
    
    # tools command
    tools_parser = subparsers.add_parser(
        "tools",
        help="List registered tools"
    )

    issue_pr_parser = subparsers.add_parser(
        "issue-pr",
        help="Run an issue -> plan -> verify -> PR workflow prompt"
    )
    issue_pr_parser.add_argument(
        "--title",
        required=False,
        help="Issue title"
    )
    issue_pr_parser.add_argument(
        "--body",
        default="",
        help="Issue body text"
    )
    issue_pr_parser.add_argument(
        "--body-file",
        default=None,
        help="Path to a file containing the issue body"
    )
    issue_pr_parser.add_argument(
        "--issue-number",
        type=int,
        default=None,
        help="GitHub issue number for reference"
    )
    issue_pr_parser.add_argument(
        "--repo-name",
        default=None,
        help="Repository name for prompt context"
    )
    issue_pr_parser.add_argument(
        "--from-gh",
        action="store_true",
        help="Fetch the issue title/body with gh issue view using --issue-number"
    )
    issue_pr_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the generated workflow prompt instead of running it"
    )
    issue_pr_parser.add_argument(
        "--use-worktree",
        action="store_true",
        help="Create an isolated git worktree and run the issue workflow inside it"
    )
    issue_pr_parser.add_argument(
        "--worktree-root-dir",
        default=None,
        help="Parent directory for worktrees created by --use-worktree"
    )
    issue_pr_parser.add_argument(
        "--create-draft-pr",
        action="store_true",
        help="After the run, create a draft PR with gh pr create using the generated PR bundle"
    )

    issue_worktree_parser = subparsers.add_parser(
        "issue-worktree",
        help="Create an isolated git worktree for an issue-driven task"
    )
    issue_worktree_parser.add_argument(
        "--repo-dir",
        default=".",
        help="Git repository directory (default: current directory)"
    )
    issue_worktree_parser.add_argument(
        "--title",
        required=True,
        help="Issue title"
    )
    issue_worktree_parser.add_argument(
        "--issue-number",
        type=int,
        default=None,
        help="Issue number for naming"
    )
    issue_worktree_parser.add_argument(
        "--base-branch",
        default="HEAD",
        help="Base branch/revision for git worktree add"
    )
    issue_worktree_parser.add_argument(
        "--root-dir",
        default=None,
        help="Parent directory for created worktrees"
    )

    # serve command
    serve_parser = subparsers.add_parser(
        "serve",
        help="Start the web dashboard server"
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=3333,
        help="Port to listen on (default: 3333)"
    )
    serve_parser.add_argument(
        "--host",
        default="localhost",
        help="Host to bind to (default: localhost)"
    )
    serve_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't open browser automatically"
    )

    return parser


def main() -> int:
    """Main entry point for the Mantis CLI."""
    # Set up signal handler for graceful Ctrl+C handling
    def signal_handler(signum, frame):
        print()
        print_system("Interrupted. Exiting...")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    
    # Parse arguments
    parser = create_parser()
    args = parser.parse_args()
    
    # Check for command
    if not args.command:
        parser.print_help()
        return 1
    
    # Build config from args and environment
    config = build_config_from_args(args)
    
    # Create MantisApp instance
    try:
        app = MantisApp(config, project_dir=args.project_dir)
    except Exception as e:
        print_error(f"Failed to initialize MantisApp: {e}")
        return 1
    
    # Route to appropriate command handler
    try:
        if args.command == "chat":
            return cmd_chat(app)
        elif args.command == "run":
            return cmd_run(app, args.prompt)
        elif args.command == "models":
            return cmd_models(app)
        elif args.command == "tools":
            return cmd_tools(app)
        elif args.command == "issue-pr":
            issue_title = args.title
            issue_body = args.body
            if args.from_gh:
                if args.issue_number is None:
                    print_error("--from-gh requires --issue-number")
                    return 1
                try:
                    issue_title, issue_body = fetch_issue_from_gh(args.issue_number, args.repo_name)
                except RuntimeError as e:
                    print_error(str(e))
                    return 1
            elif args.body_file:
                try:
                    with open(args.body_file, "r", encoding="utf-8") as f:
                        issue_body = f.read()
                except OSError as e:
                    print_error(f"Failed to read issue body file: {e}")
                    return 1
            if not issue_title:
                print_error("issue-pr requires --title unless --from-gh is used")
                return 1
            return cmd_issue_pr(
                app,
                title=issue_title,
                body=issue_body,
                issue_number=args.issue_number,
                repo_name=args.repo_name,
                dry_run=args.dry_run,
                use_worktree=args.use_worktree,
                worktree_root_dir=args.worktree_root_dir,
                create_draft_pr=args.create_draft_pr,
            )
        elif args.command == "issue-worktree":
            return cmd_issue_worktree(
                repo_dir=args.repo_dir,
                title=args.title,
                issue_number=args.issue_number,
                base_branch=args.base_branch,
                root_dir=args.root_dir,
            )
        elif args.command == "serve":
            return cmd_serve(
                host=args.host,
                port=args.port,
                open_browser=not args.no_browser,
            )
        else:
            parser.print_help()
            return 1
    except KeyboardInterrupt:
        print()
        print_system("Interrupted. Exiting...")
        return 130  # Standard exit code for SIGINT
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
