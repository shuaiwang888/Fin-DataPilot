"""Bounded loading of project-owned Skill instructions and templates.

The agent must not treat a tool name as sufficient context: before executing a
planned capability it loads the corresponding ``SKILL.md`` and any lightweight
text templates shipped with that Skill.  This module deliberately reads only
from the checked-in ``Skills/`` tree and never follows user-provided paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SKILLS_ROOT = _PROJECT_ROOT / "Skills"
_DIRECTORY_ALIASES = {"anysearch": "anysearch-skill"}
_TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".html"}


def _safe_skill_dir(skill_name: str) -> Path | None:
    """Resolve a registry name to a checked-in Skill directory, safely."""
    if not skill_name or "/" in skill_name or "\\" in skill_name or ".." in skill_name:
        return None
    directory = _DIRECTORY_ALIASES.get(skill_name, skill_name)
    candidate = (_SKILLS_ROOT / directory).resolve()
    try:
        candidate.relative_to(_SKILLS_ROOT.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_dir() else None


def _read_text(path: Path, max_chars: int) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except OSError:
        return ""


def load_skill_resources(
    skill_names: list[str], *, max_chars: int = 12_000, max_templates_per_skill: int = 4
) -> dict[str, dict[str, Any]]:
    """Return bounded instructions/templates for the requested registered Skills.

    Binary templates are represented by their path only; they are available to
    a future document/chart executor without being injected into the LLM
    context.  The total text budget prevents a plan with many Skills from
    crowding out the actual research evidence.
    """
    remaining = max(0, max_chars)
    resources: dict[str, dict[str, Any]] = {}
    for skill_name in dict.fromkeys(skill_names):
        if remaining <= 0:
            break
        skill_dir = _safe_skill_dir(skill_name)
        if skill_dir is None:
            continue

        instruction_path = skill_dir / "SKILL.md"
        instruction = _read_text(instruction_path, min(remaining, 6_000))
        remaining -= len(instruction)

        templates: list[dict[str, str]] = []
        template_dir = skill_dir / "templates"
        if template_dir.is_dir():
            for path in sorted(template_dir.rglob("*")):
                if len(templates) >= max_templates_per_skill or not path.is_file():
                    break
                relative = str(path.relative_to(skill_dir))
                if path.suffix.lower() in _TEXT_SUFFIXES and remaining > 0:
                    content = _read_text(path, min(remaining, 2_000))
                    remaining -= len(content)
                    templates.append({"path": relative, "content": content})
                else:
                    templates.append({"path": relative, "content": ""})

        resources[skill_name] = {
            "instruction": instruction,
            "templates": templates,
        }
    return resources


def render_skill_resources(resources: dict[str, dict[str, Any]], *, max_chars: int = 8_000) -> str:
    """Format loaded, project-owned resources for a routing prompt."""
    blocks: list[str] = []
    used = 0
    for name, resource in resources.items():
        instruction = str(resource.get("instruction") or "")
        templates = resource.get("templates") or []
        template_text = "\n".join(
            f"- {item.get('path', '')}: {str(item.get('content') or '')}"
            for item in templates
            if isinstance(item, dict)
        )
        block = f"## {name}\n{instruction}\n{template_text}".strip()
        if not block:
            continue
        available = max_chars - used
        if available <= 0:
            break
        blocks.append(block[:available])
        used += len(blocks[-1])
    return "\n\n".join(blocks)
