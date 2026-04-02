import os
import yaml
from typing import List
from dataclasses import dataclass

@dataclass
class Skill:
    name: str
    description: str
    content: str
    file_path: str


class SkillLoader:
    def __init__(self, skills_dir: str = "./skills"):
        self.skills_dir = skills_dir
        self._skills = {}
    
    def load_all(self) -> List[Skill]:
        """Scan skills_dir for *.md files with frontmatter"""
        self._skills = {}
        
        if not os.path.exists(self.skills_dir):
            return []
        
        for filename in os.listdir(self.skills_dir):
            if filename.lower().endswith('.md'):
                file_path = os.path.join(self.skills_dir, filename)
                try:
                    skill = self._load_skill(file_path)
                    if skill:
                        self._skills[skill.name] = skill
                except Exception:
                    continue  # Skip invalid files
        
        return list(self._skills.values())
    
    def _load_skill(self, file_path: str) -> Skill:
        """Load a single skill from a markdown file with YAML frontmatter"""
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Check if there's frontmatter
        lines = content.split('\n')
        if len(lines) < 3 or lines[0] != '---':
            return None  # Invalid format
        
        # Find end of frontmatter
        frontmatter_end_idx = -1
        for i in range(1, len(lines)):
            if lines[i].strip() == '---':
                frontmatter_end_idx = i
                break
        
        if frontmatter_end_idx == -1:
            return None  # No closing ---
        
        # Extract frontmatter and content
        frontmatter_str = '\n'.join(lines[1:frontmatter_end_idx])
        content_body = '\n'.join(lines[frontmatter_end_idx + 1:]).strip()
        
        # Parse frontmatter
        try:
            frontmatter = yaml.safe_load(frontmatter_str)
        except yaml.YAMLError:
            return None  # Invalid YAML
        
        if not isinstance(frontmatter, dict) or 'name' not in frontmatter or 'description' not in frontmatter:
            return None  # Missing required fields
        
        name = frontmatter['name']
        description = frontmatter['description']
        
        return Skill(
            name=name,
            description=description,
            content=content_body,
            file_path=file_path
        )
    
    def get(self, name: str) -> Skill:
        """Get a skill by name"""
        return self._skills.get(name)
    
    def list_all(self) -> List[Skill]:
        """List all loaded skills"""
        return list(self._skills.values())
    
    def search(self, query: str) -> List[Skill]:
        """Keyword search in name + description"""
        query_lower = query.lower()
        results = []
        
        for skill in self._skills.values():
            if (query_lower in skill.name.lower()) or (query_lower in skill.description.lower()):
                results.append(skill)
        
        return results
