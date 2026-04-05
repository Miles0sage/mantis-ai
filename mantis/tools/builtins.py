import asyncio
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Any
import glob
import fnmatch

from mantis.core.tool_registry import ToolRegistry
from mantis.memory.store import MemoryStore
from mantis.memory.search import MemorySearch
from mantis.tools.ast_extractor import (
    build_edit_context as _build_edit_context,
    extract_symbol as _extract_symbol,
    extract_symbols as _extract_symbols,
    replace_symbol as _replace_symbol,
)
from mantis.tools.edit_applicator import apply_edit as _apply_edit

_memory_store = MemoryStore()
_memory_search = MemorySearch(_memory_store)


_JS_SYMBOL_PATTERNS = [
    re.compile(r"^\s*export\s+class\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)", re.MULTILINE),
    re.compile(r"^\s*class\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)", re.MULTILINE),
    re.compile(r"^\s*export\s+function\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*\(", re.MULTILINE),
    re.compile(r"^\s*function\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*\(", re.MULTILINE),
    re.compile(r"^\s*export\s+const\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?\(", re.MULTILINE),
    re.compile(r"^\s*const\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?\(", re.MULTILINE),
    re.compile(r"^\s*export\s+const\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?[A-Za-z_$][^=]*=>", re.MULTILINE),
    re.compile(r"^\s*const\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?[A-Za-z_$][^=]*=>", re.MULTILINE),
]


def _list_js_symbols(file_path: str) -> list[dict[str, Any]]:
    try:
        source = Path(file_path).read_text(encoding="utf-8")
    except OSError:
        return []

    symbols: list[dict[str, Any]] = []
    seen: set[str] = set()
    lines = source.splitlines()

    for pattern in _JS_SYMBOL_PATTERNS:
        for match in pattern.finditer(source):
            name = match.group("name")
            if name in seen:
                continue
            seen.add(name)
            line_no = source.count("\n", 0, match.start()) + 1
            kind = "class" if "class" in match.group(0) else "function"
            preview = lines[line_no - 1].strip() if 0 <= line_no - 1 < len(lines) else ""
            symbols.append({"name": name, "kind": kind, "line": line_no, "preview": preview})

    return sorted(symbols, key=lambda item: item["line"])


def _read_js_symbol(file_path: str, symbol_name: str) -> str | None:
    try:
        source = Path(file_path).read_text(encoding="utf-8")
    except OSError:
        return None

    lines = source.splitlines()
    symbols = _list_js_symbols(file_path)
    start_line = None
    end_line = len(lines)
    for index, symbol in enumerate(symbols):
        if symbol["name"] != symbol_name:
            continue
        start_line = symbol["line"] - 1
        if index + 1 < len(symbols):
            end_line = symbols[index + 1]["line"] - 1
        break

    if start_line is None:
        return None

    return "\n".join(lines[start_line:end_line]).strip()


def _build_js_edit_context(file_path: str, task_description: str) -> str:
    symbols = _list_js_symbols(file_path)
    if not symbols:
        return f"Error: No JS/TS symbols found in '{file_path}'."
    lines = [
        f"JS/TS edit context for {file_path}",
        f"Task: {task_description}",
        "",
        "Top-level symbols:",
    ]
    for symbol in symbols[:20]:
        lines.append(f"- {symbol['kind']} {symbol['name']} (line {symbol['line']}): {symbol['preview']}")
    return "\n".join(lines)


async def read_file(file_path: str, offset: int = 0, limit: int = 2000) -> str:
    """
    Read a file and return its content with line numbers.
    
    Args:
        file_path: Path to the file to read
        offset: Line number to start reading from (0-indexed)
        limit: Maximum number of characters to return
    
    Returns:
        String containing numbered lines of the file
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        lines = content.splitlines()
        
        # Apply offset and limit
        start_idx = min(offset, len(lines))
        selected_lines = lines[start_idx:]
        
        result_lines = []
        for i, line in enumerate(selected_lines):
            line_num = start_idx + i + 1
            result_lines.append(f"{line_num:4d}: {line}")
            
            # Check if we've reached the character limit
            if sum(len(l) for l in result_lines) > limit:
                break
                
        return "\n".join(result_lines[:limit])
    except FileNotFoundError:
        return f"Error: File '{file_path}' not found."
    except PermissionError:
        return f"Error: Permission denied when reading '{file_path}'."
    except UnicodeDecodeError:
        return f"Error: Unable to decode file '{file_path}' as UTF-8."
    except Exception as e:
        return f"Error reading file '{file_path}': {str(e)}"


async def write_file(file_path: str, content: str) -> str:
    """
    Write content to a file, creating directories if needed.
    
    Args:
        file_path: Path to the file to write
        content: Content to write to the file
    
    Returns:
        Success message or error message
    """
    try:
        # Create parent directories if they don't exist
        path_obj = Path(file_path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return f"Successfully wrote {len(content)} characters to '{file_path}'."
    except PermissionError:
        return f"Error: Permission denied when writing to '{file_path}'."
    except OSError as e:
        return f"Error creating directories or writing to '{file_path}': {str(e)}"
    except Exception as e:
        return f"Error writing to file '{file_path}': {str(e)}"


async def edit_file(file_path: str, old_string: str, new_string: str) -> str:
    """
    Replace occurrences of old_string with new_string in a file.
    
    Args:
        file_path: Path to the file to edit
        old_string: String to be replaced
        new_string: Replacement string
    
    Returns:
        Success message or error message
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if old_string not in content:
            return f"No occurrence of '{old_string}' found in '{file_path}'."
        
        new_content = content.replace(old_string, new_string)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        count = content.count(old_string)
        return f"Replaced {count} occurrence(s) of '{old_string}' with '{new_string}' in '{file_path}'."
    except FileNotFoundError:
        return f"Error: File '{file_path}' not found."
    except PermissionError:
        return f"Error: Permission denied when editing '{file_path}'."
    except Exception as e:
        return f"Error editing file '{file_path}': {str(e)}"


async def run_bash(command: str, timeout: int = 120) -> str:
    """
    Run a bash command and return its output.
    
    Args:
        command: The bash command to execute
        timeout: Timeout in seconds
    
    Returns:
        Command output (stdout and stderr combined) or error message
    """
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            
            output = stdout.decode('utf-8') + stderr.decode('utf-8')
            
            # Limit output length
            if len(output) > 10000:
                output = output[:10000] + "... [output truncated]"
                
            return output
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return f"Command '{command}' timed out after {timeout} seconds."
    except Exception as e:
        return f"Error running command '{command}': {str(e)}"


async def run_tsc(path: str = ".", args: str = "--noEmit") -> str:
    """
    Run the TypeScript compiler and return diagnostics.

    Args:
        path: Directory containing tsconfig.json (default: current directory)
        args: Extra tsc arguments (default: --noEmit)

    Returns:
        Compiler output or a success message if no errors.
    """
    command = f"cd {path} && npx tsc {args} 2>&1"
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
            output = (stdout + stderr).decode("utf-8").strip()
            if not output and process.returncode == 0:
                return "tsc: no errors found."
            if len(output) > 8000:
                output = output[:8000] + "... [output truncated]"
            return output
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return f"tsc timed out after 60 seconds in '{path}'."
    except Exception as e:
        return f"Error running tsc in '{path}': {str(e)}"


async def glob_files(pattern: str, path: str = ".") -> str:
    """
    Find files matching a glob pattern.
    
    Args:
        pattern: Glob pattern to match (e.g., "*.py", "**/*.txt")
        path: Base path to search in (default is current directory)
    
    Returns:
        List of matching files or error message
    """
    try:
        full_pattern = os.path.join(path, pattern)
        matches = glob.glob(full_pattern, recursive="**" in pattern)
        
        if not matches:
            return f"No files matching pattern '{pattern}' in '{path}'."
        
        return "\n".join(sorted(matches))
    except Exception as e:
        return f"Error searching for files with pattern '{pattern}' in '{path}': {str(e)}"


async def grep_search(pattern: str, path: str = ".", include: str = None) -> str:
    """
    Search file contents using a regex pattern.
    
    Args:
        pattern: Regex pattern to search for
        path: Base path to search in (default is current directory)
        include: Optional file pattern to include (e.g., "*.py")
    
    Returns:
        List of matches with filenames and line numbers
    """
    try:
        results = []
        
        # Walk through the directory
        for root, dirs, files in os.walk(path):
            # Filter files based on include pattern if provided
            if include:
                files = [f for f in files if fnmatch.fnmatch(f, include)]
            
            for file in files:
                file_path = os.path.join(root, file)
                
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    
                    # Search for pattern in content
                    for i, line in enumerate(content.splitlines(), 1):
                        if re.search(pattern, line):
                            results.append(f"{file_path}:{i}: {line.strip()}")
                            
                except Exception:
                    # Skip files that can't be read
                    continue
        
        if not results:
            return f"No matches found for pattern '{pattern}' in '{path}'."
        
        return "\n".join(results[:50])  # Limit to first 50 results
    except re.error as e:
        return f"Invalid regex pattern '{pattern}': {str(e)}"
    except Exception as e:
        return f"Error searching for pattern '{pattern}' in '{path}': {str(e)}"


async def apply_edit(file_path: str, search_text: str, replace_text: str) -> dict:
    """
    Apply a SEARCH/REPLACE edit to a file using fuzzy matching strategies.

    Args:
        file_path: Path to the file to edit
        search_text: Text to search for (exact, flexible whitespace, or fuzzy)
        replace_text: Text to replace with

    Returns:
        Dict with keys: success (bool), strategy (str), message (str)
    """
    try:
        success = _apply_edit(file_path, search_text, replace_text)
        if success:
            return {
                "success": True,
                "strategy": "applied",
                "message": f"Successfully applied edit to '{file_path}'.",
            }
        else:
            return {
                "success": False,
                "strategy": "none",
                "message": f"Could not find search text in '{file_path}'.",
            }
    except FileNotFoundError:
        return {
            "success": False,
            "strategy": "none",
            "message": f"Error: File '{file_path}' not found.",
        }
    except Exception as e:
        return {
            "success": False,
            "strategy": "none",
            "message": f"Error applying edit to '{file_path}': {str(e)}",
        }


async def memory_save(key: str, content: str) -> str:
    try:
        _memory_store.save(key, content)
        return f"Saved memory '{key}'."
    except Exception as e:
        return f"Error saving memory '{key}': {str(e)}"


async def memory_recall(query: str) -> str:
    try:
        results = _memory_search.search(query)
        if not results:
            return f"No memories found for query '{query}'."
        lines = [f"{r.key}: {r.snippet}" for r in results]
        return "\n".join(lines)
    except Exception as e:
        return f"Error recalling memories for '{query}': {str(e)}"


async def list_python_symbols(file_path: str) -> list[dict[str, Any]]:
    """List top-level Python symbols for semantic navigation."""
    return _extract_symbols(file_path)


async def read_python_symbol(file_path: str, symbol_name: str) -> str:
    """Read a specific Python symbol by name."""
    source = _extract_symbol(file_path, symbol_name)
    if source is None:
        return f"Symbol '{symbol_name}' not found in '{file_path}'."
    return source


async def replace_python_symbol(file_path: str, symbol_name: str, new_source: str) -> dict[str, Any]:
    """Replace one Python symbol while preserving the rest of the file."""
    success = _replace_symbol(file_path, symbol_name, new_source)
    if success:
        return {
            "success": True,
            "message": f"Replaced symbol '{symbol_name}' in '{file_path}'.",
        }
    return {
        "success": False,
        "message": f"Could not replace symbol '{symbol_name}' in '{file_path}'.",
    }


async def build_python_edit_context(file_path: str, task_description: str) -> str:
    """Build focused semantic context for a Python edit task."""
    return _build_edit_context(file_path, task_description)


async def list_js_symbols(file_path: str) -> list[dict[str, Any]]:
    """List top-level JS/TS symbols for semantic navigation."""
    return _list_js_symbols(file_path)


async def read_js_symbol(file_path: str, symbol_name: str) -> str:
    """Read a specific JS/TS symbol by name."""
    source = _read_js_symbol(file_path, symbol_name)
    if source is None:
        return f"Symbol '{symbol_name}' not found in '{file_path}'."
    return source


async def build_js_edit_context(file_path: str, task_description: str) -> str:
    """Build focused semantic context for a JS/TS edit task."""
    return _build_js_edit_context(file_path, task_description)


def register_builtins(registry: ToolRegistry) -> None:
    """
    Register all builtin tools with the provided registry.
    
    Args:
        registry: ToolRegistry instance to register tools with
    """
    read_file_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the file to read"},
            "offset": {"type": "integer", "description": "Line number to start reading from (0-indexed)", "default": 0},
            "limit": {"type": "integer", "description": "Maximum number of characters to return", "default": 2000}
        },
        "required": ["file_path"]
    }
    
    write_file_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the file to write"},
            "content": {"type": "string", "description": "Content to write to the file"}
        },
        "required": ["file_path", "content"]
    }
    
    edit_file_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the file to edit"},
            "old_string": {"type": "string", "description": "String to be replaced"},
            "new_string": {"type": "string", "description": "Replacement string"}
        },
        "required": ["file_path", "old_string", "new_string"]
    }
    
    run_bash_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The bash command to execute"},
            "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 120}
        },
        "required": ["command"]
    }
    
    glob_files_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern to match (e.g., \"*.py\", \"**/*.txt\")"},
            "path": {"type": "string", "description": "Base path to search in", "default": "."}
        },
        "required": ["pattern"]
    }
    
    grep_search_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "path": {"type": "string", "description": "Base path to search in", "default": "."},
            "include": {"type": "string", "description": "Optional file pattern to include (e.g., \"*.py\")"}
        },
        "required": ["pattern"]
    }
    
    memory_save_schema = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Key to store the memory under"},
            "content": {"type": "string", "description": "Content to save"}
        },
        "required": ["key", "content"]
    }

    memory_recall_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query to find relevant memories"}
        },
        "required": ["query"]
    }

    apply_edit_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the file to edit"},
            "search_text": {"type": "string", "description": "Text to search for (supports exact, flexible whitespace, and fuzzy matching)"},
            "replace_text": {"type": "string", "description": "Text to replace the matched section with"}
        },
        "required": ["file_path", "search_text", "replace_text"]
    }

    list_python_symbols_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the Python file to inspect"}
        },
        "required": ["file_path"]
    }

    read_python_symbol_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the Python file to inspect"},
            "symbol_name": {"type": "string", "description": "Top-level function or class name to read"}
        },
        "required": ["file_path", "symbol_name"]
    }

    replace_python_symbol_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the Python file to edit"},
            "symbol_name": {"type": "string", "description": "Top-level function or class name to replace"},
            "new_source": {"type": "string", "description": "Replacement source for the symbol"}
        },
        "required": ["file_path", "symbol_name", "new_source"]
    }

    build_python_edit_context_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the Python file to inspect"},
            "task_description": {"type": "string", "description": "Short description of the edit task"}
        },
        "required": ["file_path", "task_description"]
    }

    list_js_symbols_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the JS/TS file to inspect"}
        },
        "required": ["file_path"]
    }

    read_js_symbol_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the JS/TS file to inspect"},
            "symbol_name": {"type": "string", "description": "Top-level class or function name to read"}
        },
        "required": ["file_path", "symbol_name"]
    }

    build_js_edit_context_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the JS/TS file to inspect"},
            "task_description": {"type": "string", "description": "Short description of the edit task"}
        },
        "required": ["file_path", "task_description"]
    }

    registry.register("read_file", "Read a file and return its contents with line numbers", read_file_schema, read_file)
    registry.register("write_file", "Write content to a file, creating directories if needed", write_file_schema, write_file)
    registry.register("edit_file", "Replace a string in a file with a new string", edit_file_schema, edit_file)
    registry.register("apply_edit", "Apply a SEARCH/REPLACE edit using fuzzy matching (exact, whitespace-flexible, or difflib fuzzy)", apply_edit_schema, apply_edit)
    registry.register("list_python_symbols", "List top-level Python functions and classes for semantic navigation", list_python_symbols_schema, list_python_symbols)
    registry.register("read_python_symbol", "Read a specific top-level Python function or class by name", read_python_symbol_schema, read_python_symbol)
    registry.register("replace_python_symbol", "Replace a top-level Python function or class while preserving the rest of the file", replace_python_symbol_schema, replace_python_symbol)
    registry.register("build_python_edit_context", "Build focused semantic context for a Python edit task", build_python_edit_context_schema, build_python_edit_context)
    registry.register("list_js_symbols", "List top-level JS/TS classes and functions for semantic navigation", list_js_symbols_schema, list_js_symbols)
    registry.register("read_js_symbol", "Read a specific top-level JS/TS class or function by name", read_js_symbol_schema, read_js_symbol)
    registry.register("build_js_edit_context", "Build focused semantic context for a JS/TS edit task", build_js_edit_context_schema, build_js_edit_context)
    registry.register("run_bash", "Run a shell command and return stdout+stderr", run_bash_schema, run_bash)
    registry.register("glob_files", "Find files matching a glob pattern", glob_files_schema, glob_files)
    registry.register("grep_search", "Search file contents with a regex pattern", grep_search_schema, grep_search)
    run_tsc_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory containing tsconfig.json", "default": "."},
            "args": {"type": "string", "description": "Extra tsc arguments", "default": "--noEmit"},
        },
        "required": [],
    }

    registry.register("memory_save", "Save content to persistent memory under a key", memory_save_schema, memory_save)
    registry.register("memory_recall", "Search persistent memory and return matching snippets", memory_recall_schema, memory_recall)
    registry.register("run_tsc", "Run TypeScript compiler and return diagnostics", run_tsc_schema, run_tsc)
