import json
from typing import List, Dict, Optional


class ContextCompressor:
    def __init__(self, max_tokens: int = 128000, model_adapter=None):
        self.max_tokens = max_tokens
        self.model_adapter = model_adapter

    def micro_compact(self, messages: List[Dict]) -> List[Dict]:
        """Replace old tool_result content (keep last 3) with '[Previous: used tool_name]'"""
        result = []
        tool_results_count = 0
        
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            if msg.get("role") == "tool" or (msg.get("role") == "assistant" and 
                                            isinstance(msg.get("content"), str) and 
                                            "[TOOL_CALL]" in msg.get("content", "")):
                if tool_results_count >= 3:
                    # Replace older tool results with placeholder
                    if msg.get("role") == "tool":
                        tool_name = self._extract_tool_name(msg.get("content", ""))
                        result.append({
                            "role": "tool",
                            "name": msg.get("name"),
                            "content": f"[Previous: used {tool_name}]"
                        })
                    elif "[TOOL_CALL]" in msg.get("content", ""):
                        tool_name = self._extract_tool_name(msg.get("content", ""))
                        result.append({
                            "role": "assistant",
                            "content": f"[Previous: used {tool_name}]"
                        })
                else:
                    result.append(msg)
                    tool_results_count += 1
            else:
                result.append(msg)
        
        return list(reversed(result))
    
    def _extract_tool_name(self, content: str) -> str:
        """Extract tool name from content string"""
        try:
            # Try to extract from JSON-like strings
            if '{' in content and '}' in content:
                start_idx = content.find('{')
                end_idx = content.rfind('}') + 1
                json_str = content[start_idx:end_idx]
                data = json.loads(json_str)
                if "name" in data:
                    return data["name"]
                elif "action" in data:
                    return data["action"]
            # Try to find common patterns in content
            import re
            matches = re.findall(r"(?:tool|function|action):\s*([a-zA-Z_][a-zA-Z0-9_]*)", content.lower())
            if matches:
                return matches[0]
        except Exception:
            pass
        return "unknown_tool"

    def auto_compact(self, messages: List[Dict], threshold: int = 50000) -> List[Dict]:
        """When token count exceeds threshold, save full transcript to disk, generate summary via model,
        replace all messages with system summary message."""
        current_tokens = self.estimate_tokens(messages)
        
        if current_tokens > threshold:
            # Save full transcript to disk
            self._save_transcript(messages)
            
            # Generate summary via model
            summary = self._generate_summary(messages)
            
            # Replace all messages with system summary message
            return [{"role": "system", "content": summary}]
        
        return messages

    def manual_compact(self, messages: List[Dict]) -> List[Dict]:
        """Force immediate summarization regardless of token count"""
        # Save full transcript to disk
        self._save_transcript(messages)
        
        # Generate summary via model
        summary = self._generate_summary(messages)
        
        # Replace all messages with system summary message
        return [{"role": "system", "content": summary}]

    def estimate_tokens(self, messages: List[Dict]) -> int:
        """Estimate token count using chars/4 approximation"""
        total_chars = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, dict):
                total_chars += len(str(content))
            elif isinstance(content, list):
                total_chars += len(str(content))
                
            # Also include role and other keys in estimation
            for key, value in msg.items():
                if key != "content":
                    total_chars += len(str(value))
                    
        return total_chars // 4

    def _save_transcript(self, messages: List[Dict]):
        """Save full transcript to disk"""
        import os
        import time
        from pathlib import Path
        
        # Create a directory for transcripts if it doesn't exist
        transcript_dir = Path("mantis_transcripts")
        transcript_dir.mkdir(exist_ok=True)
        
        # Generate filename with timestamp
        timestamp = str(int(time.time()))
        filepath = transcript_dir / f"transcript_{timestamp}.json"
        
        # Write messages to file
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(messages, f, indent=2)

    def _generate_summary(self, messages: List[Dict]) -> str:
        """Generate summary of the conversation preserving key elements"""
        if not self.model_adapter:
            # Fallback summary if no model adapter provided
            return self._fallback_summary(messages)
        
        # Construct a prompt to summarize the conversation
        prompt = """
Please create a concise summary of this conversation that preserves:
- Key decisions made
- Files modified
- Current task state
- Important errors encountered

Be brief but comprehensive enough to continue the task effectively.

Conversation:
"""

        # Add messages to the prompt
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, str):
                prompt += f"\n{role.upper()}: {content}\n"
            else:
                prompt += f"\n{role.upper()}: {str(content)}\n"

        # Use the model adapter to generate the summary
        try:
            response = self.model_adapter.generate(prompt)
            return response.strip()
        except Exception:
            # Fallback to simple summary if model fails
            return self._fallback_summary(messages)

    def _fallback_summary(self, messages: List[Dict]) -> str:
        """Generate a basic summary without using an AI model"""
        summary_parts = []
        
        # Extract important information from messages
        decisions = []
        files_modified = []
        errors = []
        task_state = None
        
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                # Look for decision indicators
                if any(word in content.lower() for word in ["decided", "choose", "decision", "selected"]):
                    decisions.append(content[:200] + "..." if len(content) > 200 else content)
                
                # Look for file modification indicators
                if any(word in content.lower() for word in ["file", "created", "modified", "updated", "saved"]):
                    files_modified.append(content[:150] + "..." if len(content) > 150 else content)
                
                # Look for error indicators
                if any(word in content.lower() for word in ["error", "failed", "exception", "problem"]):
                    errors.append(content[:150] + "..." if len(content) > 150 else content)
                
                # Look for task state indicators
                if any(word in content.lower() for word in ["task", "progress", "complete", "done", "working"]):
                    task_state = content[:150] + "..." if len(content) > 150 else content
        
        if decisions:
            summary_parts.append(f"Key Decisions: {'; '.join(decisions[:3])}")
        
        if files_modified:
            summary_parts.append(f"Files Modified: {'; '.join(files_modified[:3])}")
        
        if errors:
            summary_parts.append(f"Errors Encountered: {'; '.join(errors[:3])}")
        
        if task_state:
            summary_parts.append(f"Current Task State: {task_state}")
        
        if not summary_parts:
            summary_parts.append("Summary: Conversation continued without major events.")
        
        return "\n".join(summary_parts)
