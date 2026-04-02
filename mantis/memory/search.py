# mantis/memory/search.py

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from mantis.memory.store import MemoryStore


@dataclass
class MemoryIndex:
    """Compact index for memory search results (~50 tokens/result)."""
    key: str
    snippet: str
    created_at: datetime


@dataclass
class MemoryTimeline:
    """Memory with chronological neighbors for context (~200 tokens/result)."""
    key: str
    snippet: str
    created_at: datetime
    neighbors: list[str] = field(default_factory=list)


@dataclass
class Memory:
    """Full memory content (~500+ tokens/result)."""
    key: str
    content: str
    created_at: datetime


class MemorySearch:
    """
    Progressive disclosure search for memory recall.
    
    Three-layer search pattern for 10x token savings:
    1. search() - fast keyword search, returns compact index (~50 tokens/result)
    2. timeline() - chronological context around matched memories (~200 tokens/result)
    3. recall() - full content retrieval for specific keys (~500+ tokens/result)
    
    This progressive approach filters memories at each layer before
    fetching more detailed content, achieving ~10x token savings.
    """
    
    def __init__(self, store: MemoryStore):
        self.store = store
    
    def search(self, query: str, limit: int = 20) -> list[MemoryIndex]:
        """
        Fast keyword search returning compact index with key and snippet.
        
        Args:
            query: Search query string (matched against memory content)
            limit: Maximum number of results to return (default 20)
            
        Returns:
            List of MemoryIndex objects with key, snippet (first 50 chars), and created_at.
            Each result is approximately ~50 tokens.
        """
        query_lower = query.lower()
        results: list[MemoryIndex] = []
        
        for key in self.store.list_keys():
            if len(results) >= limit:
                break
            
            memory = self.store.get(key)
            if memory is None:
                continue
            
            content_lower = memory.content.lower()
            
            if query_lower in content_lower:
                snippet = memory.content[:50] if len(memory.content) > 50 else memory.content
                
                results.append(MemoryIndex(
                    key=key,
                    snippet=snippet,
                    created_at=memory.created_at
                ))
        
        return sorted(results, key=lambda x: x.created_at, reverse=True)
    
    def timeline(self, keys: list[str]) -> list[MemoryTimeline]:
        """
        Returns memories with chronological neighbors for context.
        
        Args:
            keys: List of memory keys to get timeline entries for
            
        Returns:
            List of MemoryTimeline objects with key, snippet, created_at, and neighbors.
            Each result is approximately ~200 tokens including neighbor context.
        """
        all_keys = self.store.list_keys()
        key_set = set(keys)
        
        results: list[MemoryTimeline] = []
        
        for key in keys:
            memory = self.store.get(key)
            if memory is None:
                continue
            
            neighbors = self._get_neighbors(key, all_keys)
            
            snippet = memory.content[:50] if len(memory.content) > 50 else memory.content
            
            results.append(MemoryTimeline(
                key=key,
                snippet=snippet,
                created_at=memory.created_at,
                neighbors=neighbors
            ))
        
        return sorted(results, key=lambda x: x.created_at)
    
    def recall(self, keys: list[str]) -> list[Memory]:
        """
        Full content retrieval for specific filtered keys.
        
        Args:
            keys: List of memory keys to retrieve full content for
            
        Returns:
            List of Memory objects with full content.
            Each result is approximately ~500+ tokens.
        """
        results: list[Memory] = []
        
        for key in keys:
            memory = self.store.get(key)
            if memory is None:
                continue
            
            results.append(Memory(
                key=memory.key,
                content=memory.content,
                created_at=memory.created_at
            ))
        
        return sorted(results, key=lambda x: x.created_at)
    
    def _get_neighbors(self, key: str, all_keys: list[str]) -> list[str]:
        """Get neighbor keys (previous and next) for a given memory key."""
        neighbors: list[str] = []
        
        try:
            idx = all_keys.index(key)
            
            if idx > 0:
                neighbors.append(all_keys[idx - 1])
            
            if idx < len(all_keys) - 1:
                neighbors.append(all_keys[idx + 1])
                
        except ValueError:
            pass
        
        return neighbors
    
    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimation: ~4 characters per token."""
        return len(text) // 4 + 10
    
    def generate_context(
        self,
        query: str,
        max_tokens: int = 2000,
        search_limit: int = 20,
        include_neighbors: bool = True
    ) -> str:
        """
        Auto-pipeline: search -> filter -> recall -> format as injectable context.
        
        This method orchestrates the three-layer search pattern to generate
        a context string that can be injected into prompts.
        
        Args:
            query: Search query to find relevant memories
            max_tokens: Maximum tokens for the generated context (default 2000)
            search_limit: Limit for initial search results (default 20)
            include_neighbors: Whether to include timeline neighbors (default True)
            
        Returns:
            Formatted context string with memories from most to least relevant.
            Returns empty string if no matching memories found.
        """
        indices = self.search(query, limit=search_limit)
        
        if not indices:
            return ""
        
        keys = [idx.key for idx in indices]
        
        timeline_entries = self.timeline(keys)
        
        expanded_keys: list[str] = []
        for entry in timeline_entries:
            if entry.key not in expanded_keys:
                expanded_keys.append(entry.key)
            
            if include_neighbors:
                for neighbor in entry.neighbors:
                    if neighbor not in expanded_keys:
                        expanded_keys.append(neighbor)
        
        memories = self.recall(expanded_keys)
        
        context_parts: list[str] = []
        current_tokens = 0
        
        for memory in memories:
            memory_text = f"[{memory.key}] ({memory.created_at.isoformat()}):\n{memory.content}"
            memory_tokens = self._estimate_tokens(memory_text)
            
            if current_tokens + memory_tokens > max_tokens:
                remaining_tokens = max_tokens - current_tokens
                
                if remaining_tokens > 100:
                    truncated_content = self._truncate_to_tokens(
                        memory.content,
                        remaining_tokens - 50
                    )
                    truncated_text = (
                        f"[{memory.key}] ({memory.created_at.isoformat()}):\n"
                        f"{truncated_content}"
                    )
                    context_parts.append(truncated_text)
                    current_tokens += self._estimate_tokens(truncated_text)
                
                break
            
            context_parts.append(memory_text)
            current_tokens += memory_tokens
        
        if not context_parts:
            return ""
        
        return "\n\n---\n\n".join(context_parts)
    
    def _truncate_to_tokens(self, text: str, max_token_estimate: int) -> str:
        """Truncate text to approximate token limit."""
        max_chars = max_token_estimate * 4
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "..."
    
    def search_by_date_range(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 20
    ) -> list[MemoryIndex]:
        """
        Search memories within a date range.
        
        Args:
            start_date: Start of date range (inclusive)
            end_date: End of date range (inclusive)
            limit: Maximum number of results
            
        Returns:
            List of MemoryIndex objects within the date range.
        """
        results: list[MemoryIndex] = []
        
        for key in self.store.list_keys():
            if len(results) >= limit:
                break
            
            memory = self.store.get(key)
            if memory is None:
                continue
            
            if start_date and memory.created_at < start_date:
                continue
            
            if end_date and memory.created_at > end_date:
                continue
            
            snippet = memory.content[:50] if len(memory.content) > 50 else memory.content
            
            results.append(MemoryIndex(
                key=key,
                snippet=snippet,
                created_at=memory.created_at
            ))
        
        return sorted(results, key=lambda x: x.created_at, reverse=True)
    
    def get_recent(self, limit: int = 10) -> list[Memory]:
        """
        Get the most recent memories.
        
        Args:
            limit: Maximum number of recent memories to retrieve
            
        Returns:
            List of Memory objects sorted by creation date (newest first).
        """
        all_keys = self.store.list_keys()
        sorted_keys = sorted(all_keys, key=lambda k: self.store.get(k).created_at if self.store.get(k) else datetime.min, reverse=True)
        
        return self.recall(sorted_keys[:limit])