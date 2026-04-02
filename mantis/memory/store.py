import os
import json
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, List, Dict
import yaml


@dataclass
class Memory:
    key: str
    content: str
    metadata: Dict
    created_at: datetime
    file_path: str


class MemoryStore:
    def __init__(self, memory_dir: str = ".mantis/memory"):
        self.memory_dir = memory_dir
        os.makedirs(self.memory_dir, exist_ok=True)

    def save(self, key: str, content: str, metadata: dict = None) -> None:
        if metadata is None:
            metadata = {}
        
        # Create file path
        file_path = os.path.join(self.memory_dir, f"{key}.md")
        
        # Prepare metadata with creation timestamp
        full_metadata = {
            "created_at": datetime.now().isoformat(),
            **metadata
        }
        
        # Write file with YAML frontmatter
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write("---\n")
            yaml.dump(full_metadata, f)
            f.write("---\n")
            f.write(content)
    
    def recall(self, key: str) -> Optional[Memory]:
        file_path = os.path.join(self.memory_dir, f"{key}.md")
        
        if not os.path.exists(file_path):
            return None
        
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Parse YAML frontmatter
        if content.startswith("---\n"):
            end_frontmatter_idx = content.find("\n---\n", 4)
            if end_frontmatter_idx != -1:
                frontmatter = content[4:end_frontmatter_idx]
                yaml_data = yaml.safe_load(frontmatter)
                actual_content = content[end_frontmatter_idx + 5:]
                
                # Extract metadata and creation time
                created_at_str = yaml_data.get("created_at")
                created_at = datetime.fromisoformat(created_at_str) if created_at_str else datetime.now()
                
                metadata = {k: v for k, v in yaml_data.items() if k != "created_at"}
                
                return Memory(
                    key=key,
                    content=actual_content,
                    metadata=metadata,
                    created_at=created_at,
                    file_path=file_path
                )
        
        # Fallback if no frontmatter exists
        return Memory(
            key=key,
            content=content,
            metadata={},
            created_at=datetime.now(),
            file_path=file_path
        )

    def search(self, query: str, limit: int = 10) -> List[Memory]:
        results = []
        
        for filename in os.listdir(self.memory_dir):
            if filename.endswith(".md"):
                key = filename[:-3]  # Remove .md extension
                memory = self.recall(key)
                
                if memory:
                    # Search in both content and metadata values
                    content_lower = memory.content.lower()
                    metadata_values = " ".join(str(v).lower() for v in memory.metadata.values())
                    
                    if query.lower() in content_lower or query.lower() in metadata_values:
                        results.append(memory)
                        
                        if len(results) >= limit:
                            break
        
        return results

    def list_all(self) -> List[Memory]:
        memories = []
        
        for filename in os.listdir(self.memory_dir):
            if filename.endswith(".md"):
                key = filename[:-3]  # Remove .md extension
                memory = self.recall(key)
                
                if memory:
                    memories.append(memory)
        
        return memories

    def delete(self, key: str) -> bool:
        file_path = os.path.join(self.memory_dir, f"{key}.md")
        
        if os.path.exists(file_path):
            os.remove(file_path)
            return True
        
        return False
