"""Skill frontmatter parsing for SKILL.md files."""

import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """Parse YAML frontmatter from a markdown string.
    
    Returns:
        (frontmatter_dict, remaining_body)
    """
    frontmatter: Dict[str, Any] = {}
    body = content

    if not content.startswith("---"):
        return frontmatter, body

    # Find the end of frontmatter (second ---)
    end_match = re.search(r"\n---\s*\n", content[3:])
    if not end_match:
        return frontmatter, body

    yaml_content = content[3: end_match.start() + 3]
    body = content[end_match.end() + 3:]

    try:
        import yaml
        parsed = yaml.safe_load(yaml_content)
        if isinstance(parsed, dict):
            frontmatter = parsed
    except Exception:
        # Fallback: simple key:value parsing
        for line in yaml_content.strip().split("\n"):
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            frontmatter[key.strip()] = value.strip()

    return frontmatter, body


def validate_frontmatter(frontmatter: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate frontmatter has required fields.
    
    Returns:
        (is_valid, error_message)
    """
    if "name" not in frontmatter:
        return False, "Missing required field: name"
    
    name = frontmatter["name"]
    if not isinstance(name, str) or not re.match(r"^[a-z0-9\-]+$", name):
        return False, f"Invalid name: {name}. Use lowercase, hyphens only."
    
    return True, ""


def extract_skill_conditions(frontmatter: Dict[str, Any]) -> list[str]:
    """Extract trigger conditions from skill frontmatter.
    
    Looks for:
    - triggers: list of keywords
    - conditions: list of keywords  
    - tags: list of keywords (from metadata.hermes.tags)
    """
    conditions = []
    
    # Direct triggers/conditions
    for key in ("triggers", "conditions", "keywords"):
        val = frontmatter.get(key)
        if isinstance(val, list):
            conditions.extend(str(v).lower() for v in val)
    
    # Metadata section
    metadata = frontmatter.get("metadata", {})
    if isinstance(metadata, dict):
        hermes = metadata.get("hermes", {})
        if isinstance(hermes, dict):
            tags = hermes.get("tags", [])
            if isinstance(tags, list):
                conditions.extend(str(v).lower() for v in tags)
    
    return list(set(conditions))


def extract_skill_description(frontmatter: Dict[str, Any], body: str = "") -> str:
    """Extract description from frontmatter or first paragraph."""
    # From frontmatter
    desc = frontmatter.get("description", "")
    if desc:
        return desc
    
    # From first paragraph
    if body:
        lines = body.strip().split("\n")
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#"):
                return line[:200]
    
    return ""


def get_skill_version(frontmatter: Dict[str, Any]) -> str:
    """Get skill version, default '1.0.0'."""
    return str(frontmatter.get("version", "1.0.0"))


def get_related_skills(frontmatter: Dict[str, Any]) -> list[str]:
    """Get related skills from metadata."""
    metadata = frontmatter.get("metadata", {})
    if isinstance(metadata, dict):
        hermes = metadata.get("hermes", {})
        if isinstance(hermes, dict):
            related = hermes.get("related_skills", [])
            if isinstance(related, list):
                return [str(r) for r in related]
    return []


__all__ = [
    "parse_frontmatter",
    "validate_frontmatter", 
    "extract_skill_conditions",
    "extract_skill_description",
    "get_skill_version",
    "get_related_skills",
]
