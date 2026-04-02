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
    
    registry.register("read_file", "Read a file and return its contents with line numbers", read_file_schema, read_file)
    registry.register("write_file", "Write content to a file, creating directories if needed", write_file_schema, write_file)
    registry.register("edit_file", "Replace a string in a file with a new string", edit_file_schema, edit_file)
    registry.register("run_bash", "Run a shell command and return stdout+stderr", run_bash_schema, run_bash)
    registry.register("glob_files", "Find files matching a glob pattern", glob_files_schema, glob_files)
    registry.register("grep_search", "Search file contents with a regex pattern", grep_search_schema, grep_search)
