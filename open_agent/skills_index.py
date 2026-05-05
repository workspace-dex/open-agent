"""Skill discovery, indexing, and caching for open-agent."""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .skill_frontmatter import parse_frontmatter, extract_skill_conditions

logger = logging.getLogger(__name__)

# Package skills directory
_PKG_SKILLS_DIR = Path(__file__).parent.parent / "skills"

# Cache directory
_CACHE_DIR = Path.home() / ".config" / "open-agent"
_SKILLS_INDEX_FILE = _CACHE_DIR / "skills_index.json"

# Global cache
_index_cache: Optional[Dict[str, Any]] = None
_index_timestamp: float = 0.0
_INDEX_TTL: float = 300.0  # 5 minutes


def get_skills_dirs() -> List[Path]:
    """Get all skills directories to scan."""
    dirs = []
    
    # 1. Package skills (read-only, ships with the package)
    if _PKG_SKILLS_DIR.exists():
        dirs.append(_PKG_SKILLS_DIR)
    
    # 2. User skills (~/.config/open-agent/skills/)
    user_skills = _CACHE_DIR / "skills"
    if user_skills.exists():
        dirs.append(user_skills)
    
    return dirs


def iter_skill_files(skills_dir: Path) -> List[Path]:
    """Iterate all SKILL.md files under a skills directory.
    
    Structure: skills/<category>/<name>/SKILL.md
    """
    results = []
    if not skills_dir.exists():
        return results
    
    for category_dir in skills_dir.iterdir():
        if not category_dir.is_dir():
            continue
        if category_dir.name.startswith("."):
            continue
        
        for skill_dir in category_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists():
                # Compute skill name as "category/name"
                results.append(skill_file)
    
    return results


def build_skill_index() -> Dict[str, Any]:
    """Build complete skill index across all skills dirs."""
    index: Dict[str, Any] = {
        "version": "1.0.0",
        "built_at": time.time(),
        "skills": {},
    }
    
    for skills_dir in get_skills_dirs():
        for skill_file in iter_skill_files(skills_dir):
            try:
                rel = skill_file.relative_to(skills_dir.parent.parent)
                # e.g. skills/software-development/systematic-debugging/SKILL.md
                parts = rel.parts
                if len(parts) >= 3 and parts[-1] == "SKILL.md":
                    category = parts[-3]
                    skill_name = f"{category}/{parts[-2]}"
                else:
                    continue
                
                content = skill_file.read_text(encoding="utf-8")
                frontmatter, body = parse_frontmatter(content)
                
                conditions = extract_skill_conditions(frontmatter)
                
                index["skills"][skill_name] = {
                    "name": skill_name,
                    "category": category,
                    "file": str(skill_file),
                    "description": frontmatter.get("description", ""),
                    "version": frontmatter.get("version", "1.0.0"),
                    "conditions": conditions,
                    "body_preview": body[:200].strip(),
                    "body_full": body,
                }
                
            except Exception as e:
                logger.debug("Failed to index skill %s: %s", skill_file, e)
    
    return index


def _ensure_cache_dir():
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)


def load_skills_index(force_refresh: bool = False) -> Dict[str, Any]:
    """Load skills index (from cache or rebuild)."""
    global _index_cache, _index_timestamp
    
    if not force_refresh and _index_cache is not None:
        if time.time() - _index_timestamp < _INDEX_TTL:
            return _index_cache
    
    # Try disk cache
    if not force_refresh and _SKILLS_INDEX_FILE.exists():
        try:
            age = time.time() - os.stat(_SKILLS_INDEX_FILE).st_mtime
            if age < _INDEX_TTL:
                with open(_SKILLS_INDEX_FILE) as f:
                    _index_cache = json.load(f)
                    _index_timestamp = time.time()
                    return _index_cache
        except Exception:
            pass
    
    # Rebuild
    _index_cache = build_skill_index()
    _index_timestamp = time.time()
    
    # Save to disk
    try:
        _ensure_cache_dir()
        with open(_SKILLS_INDEX_FILE, "w") as f:
            json.dump(_index_cache, f)
    except Exception as e:
        logger.debug("Failed to save skills index: %s", e)
    
    return _index_cache


def clear_skills_index():
    """Clear the in-memory and disk index cache."""
    global _index_cache, _index_timestamp
    _index_cache = None
    _index_timestamp = 0.0
    try:
        _SKILLS_INDEX_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def get_skill_index_entry(skill_name: str) -> Optional[Dict[str, Any]]:
    """Get a single skill's index entry."""
    index = load_skills_index()
    return index["skills"].get(skill_name)


def match_skills(query: str) -> List[Dict[str, Any]]:
    """Find skills whose conditions match the query keywords."""
    index = load_skills_index()
    query_lower = query.lower()
    query_words = set(query_lower.split())
    
    matched = []
    for skill_name, info in index["skills"].items():
        conditions = info.get("conditions", [])
        if not conditions:
            continue
        
        # Match if any condition word appears in the query
        if any(cond in query_words for cond in conditions):
            matched.append(info)
        elif any(cond in query_lower for cond in conditions):
            # Partial match in longer query
            matched.append(info)
    
    return matched


def get_skill_path(skill_name: str) -> Optional[Path]:
    """Get the file path for a skill by name."""
    entry = get_skill_index_entry(skill_name)
    if entry:
        return Path(entry["file"])
    return None


def get_skill_count() -> int:
    """Get total number of indexed skills."""
    index = load_skills_index()
    return len(index["skills"])


def list_all_skills() -> List[str]:
    """Get list of all skill names."""
    index = load_skills_index()
    return sorted(index["skills"].keys())


__all__ = [
    "get_skills_dirs",
    "iter_skill_files",
    "build_skill_index",
    "load_skills_index",
    "clear_skills_index",
    "get_skill_index_entry",
    "match_skills",
    "get_skill_path",
    "get_skill_count",
    "list_all_skills",
]