"""AST-based Python code extractor — extract/replace symbols for cheap model editing."""
import ast
import re
from typing import Optional


def extract_symbols(file_path: str) -> list[dict]:
    """Parse a Python file and return all top-level functions and classes."""
    try:
        with open(file_path, "r") as f:
            source = f.read()
        lines = source.splitlines()
        tree = ast.parse(source, filename=file_path)
    except (SyntaxError, FileNotFoundError, UnicodeDecodeError):
        return []

    symbols = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = node.lineno - 1  # 0-indexed
            end = node.end_lineno if hasattr(node, "end_lineno") else start + 1
            # Include decorators
            if node.decorator_list:
                start = node.decorator_list[0].lineno - 1
            source_text = "\n".join(lines[start:end])
            symbols.append({
                "name": node.name,
                "type": "class" if isinstance(node, ast.ClassDef) else "function",
                "start_line": start + 1,
                "end_line": end,
                "source": source_text,
            })
    return symbols


def extract_symbol(file_path: str, symbol_name: str) -> Optional[str]:
    """Extract source code of a specific function or class by name."""
    for sym in extract_symbols(file_path):
        if sym["name"] == symbol_name:
            return sym["source"]
    return None


def replace_symbol(file_path: str, symbol_name: str, new_source: str) -> bool:
    """Replace a specific function/class in the file, preserving everything else."""
    try:
        with open(file_path, "r") as f:
            source = f.read()
        lines = source.splitlines()
        tree = ast.parse(source, filename=file_path)
    except (SyntaxError, FileNotFoundError):
        return False

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == symbol_name:
                start = node.lineno - 1
                end = node.end_lineno if hasattr(node, "end_lineno") else start + 1
                if node.decorator_list:
                    start = node.decorator_list[0].lineno - 1

                new_lines = lines[:start] + new_source.splitlines() + lines[end:]
                with open(file_path, "w") as f:
                    f.write("\n".join(new_lines) + "\n")
                return True
    return False


def build_edit_context(file_path: str, task_description: str) -> str:
    """Build focused context with only relevant symbols for cheap model editing."""
    try:
        with open(file_path, "r") as f:
            source = f.read()
        lines = source.splitlines()
    except (FileNotFoundError, UnicodeDecodeError):
        return f"Error: cannot read {file_path}"

    # Always include imports (header)
    header_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")) or not stripped or stripped.startswith("#"):
            header_lines.append(line)
        else:
            break

    # Find relevant symbols by keyword matching
    task_words = set(re.findall(r"[a-z_]+", task_description.lower()))
    symbols = extract_symbols(file_path)

    relevant = []
    other_names = []
    for sym in symbols:
        sym_words = set(re.findall(r"[a-z_]+", sym["name"].lower()))
        source_words = set(re.findall(r"[a-z_]+", sym["source"][:200].lower()))
        overlap = len(task_words & (sym_words | source_words))
        if overlap > 0:
            relevant.append(sym)
        else:
            other_names.append(f"# {sym['type']} {sym['name']} (lines {sym['start_line']}-{sym['end_line']})")

    parts = [f"# File: {file_path}", "# Imports / header:"]
    parts.extend(header_lines)

    if relevant:
        parts.append(f"\n# Relevant symbols ({len(relevant)}/{len(symbols)}):")
        for sym in relevant:
            parts.append(f"\n{sym['source']}")
    else:
        # No keyword match — include all symbols
        parts.append("\n# All symbols:")
        for sym in symbols:
            parts.append(f"\n{sym['source']}")

    if other_names:
        parts.append(f"\n# Other symbols in file (not shown):")
        parts.extend(other_names)

    context = "\n".join(parts)
    return context
