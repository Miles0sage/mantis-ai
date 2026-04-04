import re
import difflib
import os


def parse_search_replace(llm_output):
    """
    Parse SEARCH/REPLACE blocks from LLM output.
    
    Format:
    <<<<<<< SEARCH
    file_path:path/to/file
    old_code
    =======
    new_code
    >>>>>>> REPLACE
    
    Or without explicit file_path:
    <<<<<<< SEARCH
    old_code
    =======
    new_code
    >>>>>>> REPLACE
    
    Returns:
        List of dicts with keys: file_path, search, replace
    """
    edits = []

    # Pattern 1: file_path on line before <<<<<<< SEARCH (aider format)
    # Format: path/to/file.py\n<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE
    pattern_file_before = (
        r'([^\n]+\.(?:py|js|ts|tsx|jsx|md|json|yaml|yml|sql|sh|go|rs|java|c|cpp|h))\s*\n'
        r'<<<<<<<\s*SEARCH\s*\n'
        r'([\s\S]*?)\n'
        r'=======\s*\n'
        r'([\s\S]*?)\n'
        r'>>>>>>>\s*REPLACE'
    )

    matches = re.findall(pattern_file_before, llm_output)
    for match in matches:
        file_path = match[0].strip()
        search_text = match[1].rstrip('\n')
        replace_text = match[2].rstrip('\n')
        
        edits.append({
            'file_path': file_path,
            'search': search_text,
            'replace': replace_text
        })
    
    # Pattern for blocks without file_path prefix (file_path is None)
    # Format: <<<<<<< SEARCH\nold_code\n=======\nnew_code\n>>>>>>> REPLACE
    pattern_without_filepath = (
        r'<<<<<<<\s*SEARCH\s*\n'
        r'(?!file_path:)'  # Negative lookahead - next line is NOT file_path:
        r'([\s\S]*?)\n'
        r'=======\s*\n'
        r'([\s\S]*?)\n'
        r'>>>>>>> REPLACE'
    )
    
    # Only match blocks that don't have file_path prefix
    # Use a more precise pattern that ensures we don't double-match
    pattern_no_filepath_precise = (
        r'<<<<<<<\s*SEARCH\s*\n'
        r'(file_path:[^\n]*\n)?'  # Optional file_path line
        r'([\s\S]*?)\n'  # search content
        r'=======\s*\n'
        r'([\s\S]*?)\n'
        r'>>>>>>> REPLACE'
    )
    
    matches_no_fp = re.findall(pattern_no_filepath_precise, llm_output)

    # Skip if pattern 1 already matched (avoid duplicates)
    if edits:
        return edits

    for match in matches_no_fp:
        fp_line = match[0]
        search_text = match[1].rstrip('\n')
        replace_text = match[2].rstrip('\n')
        
        # If there's a file_path prefix, skip (already handled above)
        if fp_line:
            continue
        
        # This block has no file_path
        edits.append({
            'file_path': None,
            'search': search_text,
            'replace': replace_text
        })
    
    # Re-parse without file_path to catch blocks that have content starting with text
    # (not file_path:) that weren't matched
    pattern_plain = (
        r'<<<<<<<\s*SEARCH\s*\n'
        r'(?:(?!file_path:)[^\n]+\n)?'  # Optional first line that's not file_path:
        r'([\s\S]*?)\n'
        r'=======\s*\n'
        r'([\s\S]*?)\n'
        r'>>>>>>> REPLACE'
    )
    
    # For blocks that truly have no file_path prefix and content starts immediately
    # We need to handle the case where the first line of search content 
    # is not a file_path: directive
    
    # Let's use a simpler string-based approach for better reliability
    edits = []
    
    search_marker = '<<<<<<< SEARCH'
    replace_marker = '>>>>>>> REPLACE'
    separator = '======='
    
    current_pos = 0
    while True:
        # Find next SEARCH block
        start_idx = llm_output.find(search_marker, current_pos)
        if start_idx == -1:
            break
        
        # Find the end of the SEARCH marker line
        line_start = llm_output.find('\n', start_idx)
        if line_start == -1:
            break
        line_start += 1
        
        # Find the separator
        sep_idx = llm_output.find(separator, line_start)
        if sep_idx == -1:
            break
        
        # Find the end REPLACE marker
        end_idx = llm_output.find(replace_marker, sep_idx)
        if end_idx == -1:
            break
        
        # Extract search and replace content
        search_section = llm_output[line_start:sep_idx]
        replace_section = llm_output[sep_idx + len(separator):end_idx]
        
        # Parse file_path from first line of search section if present
        file_path = None
        search_lines = search_section.split('\n', 1)
        
        if search_lines and search_lines[0].startswith('file_path:'):
            file_path = search_lines[0][len('file_path:'):].strip()
            search_text = search_lines[1] if len(search_lines) > 1 else ''
        else:
            search_text = search_section
        
        edits.append({
            'file_path': file_path,
            'search': search_text.rstrip('\n'),
            'replace': replace_section.strip()
        })
        
        current_pos = end_idx + len(replace_marker)
    
    return edits


def apply_edit(file_path, search_text, replace_text):
    """
    Apply an edit to a file.
    
    Tries matching strategies in order:
    1. Exact match
    2. Flexible whitespace match (strip leading whitespace)
    3. Fuzzy match using difflib.SequenceMatcher with 0.8 threshold
    
    Args:
        file_path: Path to the file to edit
        search_text: Text to search for
        replace_text: Text to replace with
        
    Returns:
        True if edit was applied, False otherwise
    """
    if not os.path.exists(file_path):
        return False
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Strategy 1: Exact match
    if search_text in content:
        content = content.replace(search_text, replace_text, 1)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    
    # Strategy 2: Flexible whitespace match
    # Strip leading whitespace from each line and try to match
    search_lines = search_text.split('\n')
    search_stripped = '\n'.join(line.lstrip() for line in search_lines)
    
    content_lines = content.split('\n')
    content_stripped = '\n'.join(line.lstrip() for line in content_lines)
    
    if search_stripped in content_stripped:
        # Find the actual positions in original content
        # We need to find the block in original content that matches stripped version
        for i in range(len(content_lines)):
            if i + len(search_lines) > len(content_lines):
                break
            block = '\n'.join(content_lines[i:i + len(search_lines)])
            block_stripped = '\n'.join(line.lstrip() for line in content_lines[i:i + len(search_lines)])
            if block_stripped == search_stripped:
                # Found match at line i
                # Replace this block
                new_lines = replace_text.split('\n')
                # Preserve indentation from original first line
                indent = len(content_lines[i]) - len(content_lines[i].lstrip())
                indented_new_lines = []
                for j, new_line in enumerate(new_lines):
                    if j == 0:
                        indented_new_lines.append(new_line)
                    else:
                        # Apply same indentation as original
                        if new_line.strip():  # Only indent non-empty lines
                            indented_new_lines.append(' ' * indent + new_line.lstrip())
                        else:
                            indented_new_lines.append('')
                
                content_lines[i:i + len(search_lines)] = indented_new_lines
                content = '\n'.join(content_lines)
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                return True
    
    # Strategy 3: Fuzzy match using difflib.SequenceMatcher
    search_normalized = search_text.replace('\n', ' \\n ').replace(' ', '  ')
    content_normalized = content.replace('\n', ' \\n ').replace(' ', '  ')
    
    search_seq = search_normalized
    matcher = difflib.SequenceMatcher(None, search_seq, content_normalized)
    
    best_match_ratio = 0
    best_match_pos = -1
    best_match_len = 0
    
    match_blocks = matcher.get_matching_blocks()
    
    for match in match_blocks:
        if match.size > 0:
            # Get the matched text
            matched_text = content_normalized[match.b:match.b + match.size]
            
            # Compare with search
            seq_matcher = difflib.SequenceMatcher(None, search_seq, matched_text)
            ratio = seq_matcher.ratio()
            
            if ratio >= 0.8 and ratio > best_match_ratio:
                best_match_ratio = ratio
                best_match_pos = match.b
                best_match_len = match.size
    
    if best_match_ratio >= 0.8 and best_match_pos >= 0:
        # Apply fuzzy match
        # Convert normalized positions back to original content positions
        # This is approximate - we use the matched block from content
        matched_block = content_normalized[best_match_pos:best_match_pos + best_match_len]
        
        # Find where this appears in original content
        orig_pos = content.find(matched_block.replace(' \\n ', '\n').replace('  ', ' '))
        if orig_pos == -1:
            # Try more lenient search
            matched_simple = matched_block.replace(' \\n ', '\n').replace('  ', ' ')
            orig_pos = content.find(matched_simple)
        
        if orig_pos >= 0:
            orig_end = orig_pos + len(matched_simple)
            content = content[:orig_pos] + replace_text + content[orig_end:]
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
    
    return False


def preview_apply_edit(file_path: str, search_text: str, replace_text: str):
    """
    Preview what apply_edit would produce without writing the file.

    Returns:
        (before, after) tuple of strings if the search text matches, else None.
    """
    if not os.path.exists(file_path):
        return None

    with open(file_path, 'r', encoding='utf-8') as f:
        before = f.read()

    # Strategy 1: Exact match
    if search_text in before:
        after = before.replace(search_text, replace_text, 1)
        return before, after

    # Strategy 2: Flexible whitespace match
    search_lines = search_text.split('\n')
    search_stripped = '\n'.join(line.lstrip() for line in search_lines)
    content_lines = before.split('\n')

    for i in range(len(content_lines)):
        if i + len(search_lines) > len(content_lines):
            break
        block_stripped = '\n'.join(line.lstrip() for line in content_lines[i:i + len(search_lines)])
        if block_stripped == search_stripped:
            indent = len(content_lines[i]) - len(content_lines[i].lstrip())
            new_lines = []
            for j, new_line in enumerate(replace_text.split('\n')):
                if j == 0:
                    new_lines.append(new_line)
                else:
                    new_lines.append((' ' * indent + new_line.lstrip()) if new_line.strip() else '')
            after_lines = content_lines[:i] + new_lines + content_lines[i + len(search_lines):]
            return before, '\n'.join(after_lines)

    return None


def apply_all_edits(edits):
    """
    Apply a list of edits in order.
    
    Args:
        edits: List of dicts with keys: file_path, search, replace
        
    Returns:
        Dict with:
            - applied: Number of successfully applied edits
            - failed: Number of failed edits
            - errors: List of error messages for failed edits
    """
    results = {
        'applied': 0,
        'failed': 0,
        'errors': []
    }
    
    for idx, edit in enumerate(edits):
        file_path = edit.get('file_path')
        search_text = edit.get('search', '')
        replace_text = edit.get('replace', '')
        
        # Validate file_path
        if not file_path:
            error_msg = f"Edit {idx + 1}: No file_path specified"
            results['failed'] += 1
            results['errors'].append(error_msg)
            continue
        
        # Check file exists
        if not os.path.exists(file_path):
            error_msg = f"Edit {idx + 1}: File not found: {file_path}"
            results['failed'] += 1
            results['errors'].append(error_msg)
            continue
        
        # Validate search text
        if not search_text:
            error_msg = f"Edit {idx + 1}: Empty search text for file: {file_path}"
            results['failed'] += 1
            results['errors'].append(error_msg)
            continue
        
        # Attempt to apply edit
        if apply_edit(file_path, search_text, replace_text):
            results['applied'] += 1
        else:
            error_msg = f"Edit {idx + 1}: Could not find search text in file: {file_path}"
            results['failed'] += 1
            results['errors'].append(error_msg)
    
    return results
