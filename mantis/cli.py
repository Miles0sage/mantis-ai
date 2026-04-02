# mantis/cli.py
import argparse
import os
import sys
import signal
from typing import Dict, Any, Optional

from mantis.app import MantisApp


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


def create_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser."""
    parser = argparse.ArgumentParser(
        prog="mantis",
        description="Mantis AI Agent Framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  mantis chat                              Start interactive REPL
  mantis run "What is the weather?"         Run single prompt
  mantis models                             List available models
  mantis tools                              List available tools
  mantis run "Analyze this" --model gpt-4   Run with specific model
  mantis chat --complexity hard            Start chat with high complexity
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
        app = MantisApp(config)
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