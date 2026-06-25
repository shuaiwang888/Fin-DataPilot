"""Runtime user-uploaded skills: install / uninstall / startup reload.

Two kinds of skills are supported:

  - **Code skill** (zip with a <name>.py handler): the handler is
    importlib-loaded, registers its own ToolSpec, and calls a real API.

  - **Prompt skill** (zip with ONLY a SKILL.md): the SKILL.md body is
    injected into the LLM's system prompt as domain knowledge. No code
    is loaded. A synthetic echo handler is registered so the skill
    appears in /api/skills and can be "tested" (returns the body).

Zip layouts accepted (auto-detected by content, not by layout):
  (a)  <name>/
         SKILL.md               (required, frontmatter: name, description)
         <name>.py              (optional, only for code skills)
         [other files]          (optional, ignored)
  (b)  SKILL.md                 (flat — root of the zip)
         [other files]          (optional, ignored)

The on-disk storage is always a directory: /data/user_skills/<name>/
containing SKILL.md (and, for code skills, <name>.py). This keeps
uninstall and reload paths uniform.

Lifecycle:
  1. install_skill_from_zip(): extract → validate → move to user_skills_dir
     → either exec the handler module (code) or register an echo handler
     with the body (prompt). Roll back on any failure.
  2. uninstall_skill(name): REGISTRY.unregister + rmtree the directory.
  3. load_uploaded_skills_at_startup(): walk user_skills_dir at app
     startup, re-import every previously uploaded skill so they
     survive container restarts.

Security notes:
  - Upload endpoint is gated by the global X-API-Key (admin-only).
  - Zip path-traversal blocked (every entry must resolve under temp).
  - Zip size capped via max_skill_upload_bytes.
  - Code handler is py_compile-validated before import.
  - Built-in skills are immutable (cannot be deleted).
"""
from __future__ import annotations

import importlib.util
import io
import logging
import py_compile
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.skills.base import ToolResult, ToolSpec
from app.skills.registry import REGISTRY

logger = logging.getLogger(__name__)

# Built-in skills cannot be overwritten or deleted via the upload API.
BUILTIN_SKILLS: frozenset[str] = frozenset(
    {
        "financial-query",
        "news-search",
        "announcement-search",
        "report-search",
    }
)


# ----------------- frontmatter -----------------

_FRONTMATTER_RE = re.compile(r"\s*---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[str | None, str | None, str | None]:
    """Extract (name, displayName, description) from YAML frontmatter.

    Tolerant of bad input — we don't want to crash the uploader over a
    typo. Each field is optional. If `name:` is missing or there's no
    frontmatter block, returns (None, None, None).
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None, None, None
    name = display_name = description = None
    for line in m.group(1).splitlines():
        if not name:
            kv = re.match(r"\s*name\s*:\s*(.*?)\s*$", line)
            if kv:
                name = kv.group(1).strip()
                continue
        if not display_name:
            kv = re.match(r"\s*displayName\s*:\s*(.*?)\s*$", line)
            if kv:
                display_name = kv.group(1).strip()
                continue
        if not description:
            kv = re.match(r"\s*description\s*:\s*(.*?)\s*$", line)
            if kv:
                description = kv.group(1).strip()
                continue
    return name, display_name, description


def _parse_frontmatter_name(md_text: str) -> str | None:
    """Backwards-compat shim — returns just the name. New code should
    use _parse_frontmatter() directly."""
    name, _, _ = _parse_frontmatter(md_text)
    return name


# ----------------- zip layout detection -----------------

@dataclass
class _LocatedSkill:
    """Result of finding the skill in the extracted zip."""
    skill_md: Path          # absolute path to SKILL.md
    name: str               # the skill's identity (from dir name or frontmatter)
    has_py: bool            # True if a code handler exists


def _locate_skill(tmp_path: Path) -> _LocatedSkill:
    """Find the SKILL.md in the extracted zip and decide which layout
    it uses. Raises ValueError on illegal layouts.

    Layouts accepted:
      (a) tmp_path/<dir>/SKILL.md            (and optionally <dir>/<dir>.py)
      (b) tmp_path/SKILL.md                 (flat — no .py allowed)

    Layouts REJECTED:
      - No SKILL.md at all
      - More than one SKILL.md
      - Two top-level directories when SKILL.md isn't at root
      - Flat layout that ALSO has stray subdirs
    """
    md_candidates = list(tmp_path.rglob("SKILL.md"))
    if not md_candidates:
        raise ValueError("No SKILL.md found in zip")
    if len(md_candidates) > 1:
        raise ValueError("Multiple SKILL.md files in zip — exactly one is allowed")
    skill_md = md_candidates[0]

    # Layout (b): SKILL.md directly in the zip root
    if skill_md.parent == tmp_path:
        # Reject if there are also subdirectories (would be ambiguous)
        stray_dirs = [p for p in tmp_path.iterdir() if p.is_dir()]
        if stray_dirs:
            raise ValueError(
                "Zip mixes a top-level SKILL.md with subdirectories — "
                "pick one layout (flat with SKILL.md only, OR one top-level dir)"
            )
        # Name must come from frontmatter (no directory to derive it from)
        name, _, _ = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        if not name:
            raise ValueError(
                "Flat zip layout (no top-level dir) requires SKILL.md "
                "frontmatter to include `name:`"
            )
        return _LocatedSkill(skill_md=skill_md, name=name, has_py=False)

    # Layout (a): SKILL.md is inside a top-level dir
    top_level = [p for p in tmp_path.iterdir() if p.is_dir()]
    if len(top_level) != 1:
        raise ValueError(
            "Zip must contain exactly one top-level directory "
            "(or a single SKILL.md at the root)"
        )
    skill_dir = top_level[0]
    if not skill_dir.name or skill_dir.name.startswith("."):
        raise ValueError(f"Invalid skill directory name: '{skill_dir.name}'")
    matching_py = (skill_dir / f"{skill_dir.name}.py").exists()
    other_py = [p for p in skill_dir.glob("*.py") if p.name != f"{skill_dir.name}.py"]
    if other_py:
        # A .py exists but its name doesn't match the directory — this
        # is almost certainly a user mistake (typo in filename). Refuse
        # rather than silently treat as prompt-only.
        names = ", ".join(p.name for p in other_py)
        raise ValueError(
            f"Handler file in {skill_dir.name}/ must be named "
            f"'{skill_dir.name}.py' (found: {names})"
        )
    return _LocatedSkill(skill_md=skill_md, name=skill_dir.name, has_py=matching_py)


# ----------------- validation -----------------

def _validate_code_handler(skill_dir: Path, name: str) -> None:
    """For code skills: ensure the handler file exists and is syntactically
    valid Python. Raises ValueError on any problem."""
    handler = skill_dir / f"{name}.py"
    if not handler.exists():
        raise ValueError(f"Handler file '{handler.name}' missing")
    try:
        py_compile.compile(str(handler), doraise=True)
    except py_compile.PyCompileError as e:
        raise ValueError(f"Handler has Python syntax errors: {e}")


def _build_prompt_handler(name: str, body: str):
    """Closure factory: returns an async handler that yields the SKILL.md
    body as its result. The default args capture `body` and `name` to
    avoid late-binding issues in the loop."""
    async def _prompt_handler(**_) -> ToolResult:
        return ToolResult(
            tool=name,
            ok=True,
            data={"skill_body": body},
            meta={"prompt_only": True},
        )
    return _prompt_handler


def _build_prompt_spec(name: str, display_name: str | None, description: str | None) -> ToolSpec:
    return ToolSpec(
        name=name,
        display_name=display_name or name,
        description=description or "(prompt-only skill — no description provided)",
        category="user",
        parameters=[],   # explicitly empty for prompt skills
        requires=[],
        enabled_by_default=True,
        version="0.1.0",
    )


# ----------------- install / uninstall / reload -----------------

def install_skill_from_zip(zip_bytes: bytes) -> dict[str, Any]:
    """Extract, validate, hot-load one skill (code OR prompt). Returns
    the SkillItem dict on success. Raises ValueError on any failure
    (with full rollback — no files left behind, no REGISTRY state mutated)."""
    settings = get_settings()
    target_root = Path(settings.user_skills_dir)
    max_bytes = settings.max_skill_upload_bytes

    with tempfile.TemporaryDirectory(prefix="skill_upload_") as tmp:
        tmp_path = Path(tmp)

        # Phase 1: pre-flight + extract. Reject path traversal and zip
        # bombs BEFORE any file lands on disk.
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            total_uncompressed = 0
            for info in zf.infolist():
                member_target = (tmp_path / info.filename).resolve()
                if not str(member_target).startswith(str(tmp_path.resolve()) + "/") and \
                   member_target != tmp_path.resolve():
                    raise ValueError(f"Unsafe path in zip: {info.filename}")
                total_uncompressed += info.file_size
                if total_uncompressed > max_bytes:
                    raise ValueError(
                        f"Zip contents exceed {max_bytes // (1024*1024)} MB limit"
                    )
            zf.extractall(tmp_path)

        # Phase 2: locate SKILL.md and decide kind.
        located = _locate_skill(tmp_path)

        # Phase 3: name conflicts
        if located.name in BUILTIN_SKILLS:
            raise ValueError(f"'{located.name}' conflicts with a built-in skill")
        if REGISTRY.get_spec(located.name) is not None:
            raise ValueError(f"Skill '{located.name}' is already registered")
        dest = target_root / located.name
        if dest.exists():
            raise ValueError(
                f"Skill '{located.name}' was already uploaded previously"
            )

        # Phase 4: parse frontmatter from the located SKILL.md and verify
        # the name matches what the directory told us.
        md_text = located.skill_md.read_text(encoding="utf-8")
        fm_name, fm_display, fm_description = _parse_frontmatter(md_text)
        if not fm_name:
            raise ValueError("SKILL.md frontmatter missing 'name' field")
        if fm_name != located.name:
            raise ValueError(
                f"SKILL.md name '{fm_name}' does not match directory/file '{located.name}'"
            )

        # Phase 5: layout-specific validation + move into the persistent root.
        # Final on-disk layout is ALWAYS: target_root/<name>/ containing
        # SKILL.md (and <name>.py for code skills).
        if located.has_py:
            # Layout (a) with .py: the whole skill_dir is the dir we want.
            # Validate BEFORE moving (file is at its temp location now).
            _validate_code_handler(located.skill_md.parent, located.name)
            # Move the whole top-level dir into target_root/<name>/
            shutil.move(str(located.skill_md.parent), str(dest))
            skill_dir = dest
            # The handler path is now under dest (recompute — the temp
            # location no longer exists after the move).
            handler_path = dest / f"{located.name}.py"
        else:
            # Layout (a) without .py OR layout (b) — both end up as
            # a single-file directory: target_root/<name>/SKILL.md
            dest.mkdir(parents=True, exist_ok=False)
            shutil.move(str(located.skill_md), str(dest / "SKILL.md"))
            skill_dir = dest
            handler_path = None  # explicit: there is no handler

    # Phase 6: register in REGISTRY.
    body = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    if handler_path is not None:
        # Code skill: py_compile already ran in Phase 5; just exec.
        if str(target_root) not in sys.path:
            sys.path.insert(0, str(target_root))
        pre_count = len(REGISTRY.list_specs())
        try:
            _exec_skill_module(located.name, handler_path)
        except Exception as e:
            shutil.rmtree(dest, ignore_errors=True)
            logger.exception("Uploaded code skill '%s' raised on import", located.name)
            raise ValueError(
                f"Handler raised on import: {type(e).__name__}: {e}"
            ) from e
        if len(REGISTRY.list_specs()) == pre_count:
            shutil.rmtree(dest, ignore_errors=True)
            raise ValueError(
                "Handler module loaded but did not call REGISTRY.register(...). "
                "Make sure the file ends with REGISTRY.register(SPEC, handler)."
            )
    else:
        # Prompt skill: register a synthetic echo handler.
        spec = _build_prompt_spec(located.name, fm_display, fm_description)
        handler = _build_prompt_handler(located.name, body)
        REGISTRY.register(spec, handler)
        # Stash the body so to_prompt_text() can inject it into the system prompt.
        REGISTRY.set_prompt_body(located.name, body)

    spec = REGISTRY.get_spec(located.name)
    if spec is None:
        # Belt-and-braces; should be impossible after the steps above.
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError("Internal: registry state inconsistent after install")

    return {
        "name": located.name,
        "spec": spec.model_dump(),
        "enabled": REGISTRY.is_enabled(located.name),
        "uploaded": True,
        "kind": "prompt" if handler_path is None else "code",
    }


def uninstall_skill(name: str) -> None:
    """Remove an uploaded skill from REGISTRY and delete its directory.
    Built-in skills are refused."""
    if name in BUILTIN_SKILLS:
        raise ValueError(f"Cannot delete built-in skill '{name}'")
    if REGISTRY.get_spec(name) is None:
        raise ValueError(f"Skill '{name}' is not registered")
    REGISTRY.unregister(name)
    REGISTRY.set_prompt_body(name, None)
    target = Path(get_settings().user_skills_dir) / name
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


def load_uploaded_skills_at_startup() -> int:
    """Re-register every previously uploaded skill so they survive
    container restarts. Returns the count successfully loaded."""
    settings = get_settings()
    root = Path(settings.user_skills_dir)
    if not root.exists():
        return 0
    loaded = 0
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in BUILTIN_SKILLS:
            logger.warning(
                "Uploaded skill dir named '%s' shadows a built-in; skipping",
                entry.name,
            )
            continue
        if REGISTRY.get_spec(entry.name) is not None:
            continue
        skill_md = entry / "SKILL.md"
        handler_py = entry / f"{entry.name}.py"
        if not skill_md.exists():
            logger.warning(
                "Uploaded skill '%s' missing SKILL.md; skipping",
                entry.name,
            )
            continue
        try:
            if handler_py.exists():
                # Code skill: importlib exec
                if str(root) not in sys.path:
                    sys.path.insert(0, str(root))
                _exec_skill_module(entry.name, handler_py)
            else:
                # Prompt skill: rehydrate spec + echo handler from the
                # saved SKILL.md body.
                body = skill_md.read_text(encoding="utf-8")
                _name, _display, _desc = _parse_frontmatter(body)
                spec = _build_prompt_spec(entry.name, _display, _desc)
                handler = _build_prompt_handler(entry.name, body)
                REGISTRY.register(spec, handler)
                REGISTRY.set_prompt_body(entry.name, body)
            loaded += 1
            logger.info("Reloaded uploaded skill '%s'", entry.name)
        except Exception:
            logger.exception(
                "Failed to reload uploaded skill '%s'; leaving on disk for manual inspection",
                entry.name,
            )
    return loaded


def _exec_skill_module(name: str, file_path: Path) -> None:
    """Load a single skill's handler file as a top-level module and
    execute it, expecting it to call REGISTRY.register(...) as a
    side-effect.

    Why not importlib.import_module(name) directly?
      Because the handler lives inside a subdirectory (e.g.
      /data/user_skills/my_skill/my_skill.py) and Python 3 treats
      directories-without-__init__.py as namespace packages, so
      `import my_skill` would resolve to an EMPTY namespace package
      and the handler file would never be executed. Loading by file
      path via spec_from_file_location sidesteps that entirely.
    """
    spec = importlib.util.spec_from_file_location(name, str(file_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not build import spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
