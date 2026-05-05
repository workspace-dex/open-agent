"""Lazy skill loader for open-agent. Skills are loaded on-demand, not at startup."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import skills_index as idx
from .skill_frontmatter import parse_frontmatter

# In-memory cache for loaded skill content
_skills_cache: Dict[str, Dict[str, Any]] = {}

# Pattern to find {{ skill:name }} references in SOUL.md or other text
_SKILL_REF_RE = re.compile(r"\{\{\s*skill:([^\}]+?)\s*\}\}")


@dataclass
class LoadedSkill:
    """A loaded skill with its metadata and body content."""
    name: str
    category: str
    description: str
    version: str
    conditions: List[str]
    content: str
    
    def to_context_block(self, max_chars: int = 2000) -> str:
        """Format as a context injection block."""
        preview = self.content[:max_chars]
        return (
            f"[Skill: {self.name}]\n"
            f"{self.description}\n\n"
            f"{preview}"
        )


def load_skill(skill_name: str, force_reload: bool = False) -> Optional[LoadedSkill]:
    """Load a skill by name, returning None if not found.
    
    Uses in-memory cache unless force_reload=True.
    """
    global _skills_cache
    
    if not force_reload and skill_name in _skills_cache:
        info = _skills_cache[skill_name]
        return LoadedSkill(
            name=info["name"],
            category=info["category"],
            description=info["description"],
            version=info["version"],
            conditions=info["conditions"],
            content=info["content"],
        )
    
    entry = idx.get_skill_index_entry(skill_name)
    if not entry:
        return None
    
    try:
        skill_path = Path(entry["file"])
        if not skill_path.exists():
            return None
        
        content = skill_path.read_text(encoding="utf-8")
        frontmatter, body = parse_frontmatter(content)
        
        skill = LoadedSkill(
            name=skill_name,
            category=entry["category"],
            description=entry["description"],
            version=entry["version"],
            conditions=entry.get("conditions", []),
            content=body.strip(),
        )
        
        _skills_cache[skill_name] = {
            "name": skill.name,
            "category": skill.category,
            "description": skill.description,
            "version": skill.version,
            "conditions": skill.conditions,
            "content": skill.content,
        }
        
        return skill
        
    except Exception:
        return None


def reload_skill(skill_name: str) -> Optional[LoadedSkill]:
    """Reload a skill, bypassing cache."""
    # Clear from cache
    if skill_name in _skills_cache:
        del _skills_cache[skill_name]
    # Rebuild index to pick up file changes
    idx.clear_skills_index()
    # Load fresh
    return load_skill(skill_name, force_reload=True)


def clear_skills_cache():
    """Clear all cached skill content."""
    global _skills_cache
    _skills_cache.clear()


def find_soul_skill_refs(text: str) -> List[str]:
    """Find all {{ skill:name }} references in text (SOUL.md etc)."""
    matches = _SKILL_REF_RE.findall(text)
    return list(set(m.strip() for m in matches))


def load_skill_from_ref(skill_ref: str) -> Optional[LoadedSkill]:
    """Load a skill from a SOUL.md {{ skill:name }} reference."""
    # Normalize the reference
    ref = skill_ref.strip()
    # Remove leading/trailing whitespace and normalize
    return load_skill(ref)


def get_matching_skills(query: str, soul_content: str = "") -> List[LoadedSkill]:
    """Get all skills that match query keywords AND/OR SOUL.md references.
    
    This is the main entry point. It:
    1. Finds skills whose trigger conditions match query words
    2. Loads skills explicitly referenced in SOUL.md via {{ skill:... }}
    
    Deduplicates. Max 3 skills per query.
    """
    skills: Dict[str, LoadedSkill] = {}
    
    # 1. SOUL.md references (highest priority — explicit)
    refs = find_soul_skill_refs(soul_content)
    for ref in refs:
        skill = load_skill(ref)
        if skill and len(skills) < 3:
            skills[skill.name] = skill
    
    # 2. Condition matching (on-demand)
    matched = idx.match_skills(query)
    for info in matched:
        if len(skills) >= 3:
            break
        skill = load_skill(info["name"])
        if skill and skill.name not in skills:
            skills[skill.name] = skill
    
    return list(skills.values())


def skill_to_context_blocks(query: str, soul_content: str = "", max_chars: int = 1500) -> str:
    """Build skill context blocks for injection into the prompt.
    
    Returns a string like:
        [Skill: systematic-debugging]
        Debugging instructions...
        
        [Skill: writing-plans]
        Planning instructions...
    """
    skills = get_matching_skills(query, soul_content)
    if not skills:
        return ""
    
    blocks = [s.to_context_block(max_chars=max_chars) for s in skills]
    return "\n\n" + "\n\n".join(blocks)


def list_available_skills() -> List[Dict[str, str]]:
    """List all available skills with metadata."""
    names = idx.list_all_skills()
    result = []
    for name in names:
        entry = idx.get_skill_index_entry(name)
        if entry:
            result.append({
                "name": name,
                "description": entry.get("description", ""),
                "conditions": ", ".join(entry.get("conditions", [])[:5]),
            })
    return result


__all__ = [
    "LoadedSkill",
    "load_skill",
    "reload_skill",
    "clear_skills_cache",
    "find_soul_skill_refs",
    "load_skill_from_ref",
    "get_matching_skills",
    "match_skills",  # alias from skills_index
    "skill_to_context_blocks",
    "list_available_skills",
]

# Re-export match_skills from skills_index for convenience
match_skills = idx.match_skills