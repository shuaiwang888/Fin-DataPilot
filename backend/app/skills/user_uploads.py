"""Runtime user-uploaded skills: install / uninstall / startup reload.

A "user skill" is a zip uploaded via POST /api/skills/upload. The zip
must contain exactly one top-level directory named <skill_name> with:
  - SKILL.md           (frontmatter: name=skill_name, description=...)
  - <skill_name>.py    (async handler + ToolSpec + REGISTRY.register call)
  - anything else is allowed but ignored

Lifecycle:
  1. install_skill_from_zip(): extract → validate → move to user_skills_dir
     → importlib.import_module() → expect side-effect REGISTRY.register
     → return SkillItem. Roll back on ANY failure.
  2. uninstall_skill(name): REGISTRY.unregister + rmtree the directory.
  3. load_uploaded_skills_at_startup(): walk user_skills_dir at app
     startup, re-import every previously uploaded skill so they
     survive container restarts.

Security notes:
  - Upload endpoint is gated by the global X-API-Key (admin-only).
  - Zip path-traversal blocked (every entry must resolve under temp).
  - Zip size capped via max_skill_upload_bytes.
  - Handler is py_compile-validated before import.
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
from pathlib import Path
from typing import Any

from app.config import get_settings
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


def _parse_frontmatter_name(md_text: str) -> str | None:
    """Extract `name:` from a YAML frontmatter block. Returns None if
    the block is missing or has no `name` field. Tolerant of bad input —
    we don't want to crash the uploader over a typo."""
    m = re.match(r"\s*---\s*\n(.*?)\n---\s*\n", md_text, re.DOTALL)
    if not m:
        return None
    for line in m.group(1).splitlines():
        # Naive YAML: only `name: value` (no quoted strings, no lists).
        # We don't need a full YAML parser for a single field.
        kv = re.match(r"\s*name\s*:\s*([^\s#]+)", line)
        if kv:
            return kv.group(1).strip()
    return None


def _validate_skill_dir(skill_dir: Path) -> None:
    """Validate a freshly extracted skill directory. Raises ValueError
    with a user-facing message on any problem."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise ValueError("SKILL.md missing in skill directory")
    try:
        md_text = skill_md.read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"Cannot read SKILL.md: {e}") from e
    name = _parse_frontmatter_name(md_text)
    if not name:
        raise ValueError("SKILL.md frontmatter missing 'name' field")
    if name != skill_dir.name:
        raise ValueError(
            f"SKILL.md name '{name}' does not match directory '{skill_dir.name}'"
        )
    handler = skill_dir / f"{skill_dir.name}.py"
    if not handler.exists():
        raise ValueError(f"Handler file '{handler.name}' missing")
    try:
        py_compile.compile(str(handler), doraise=True)
    except py_compile.PyCompileError as e:
        raise ValueError(f"Handler has Python syntax errors: {e}")


def install_skill_from_zip(zip_bytes: bytes) -> dict[str, Any]:
    """Extract, validate, hot-load one skill. Returns the SkillItem dict
    on success. Raises ValueError on any failure (with full rollback —
    no files left behind, no REGISTRY state mutated)."""
    settings = get_settings()
    target_root = Path(settings.user_skills_dir)
    max_bytes = settings.max_skill_upload_bytes

    # Phase 1: extract to a temp dir. We do NOT touch target_root until
    # we know the zip is well-formed AND the directory is unique AND
    # the contents validate. Rollback is just "rmtree the temp dir".
    with tempfile.TemporaryDirectory(prefix="skill_upload_") as tmp:
        tmp_path = Path(tmp)

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            # Pre-flight: walk every entry, reject path traversal and
            # total uncompressed size bombs BEFORE extracting anything.
            total_uncompressed = 0
            for info in zf.infolist():
                # Resolve to absolute path; check it's still under tmp.
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

        # Phase 2: shape check — exactly one top-level directory.
        top_level_entries = [p for p in tmp_path.iterdir() if p.is_dir()]
        if len(top_level_entries) != 1:
            raise ValueError(
                "Zip must contain exactly one top-level directory (the skill folder)"
            )
        skill_dir_name = top_level_entries[0].name
        if not skill_dir_name or skill_dir_name.startswith("."):
            raise ValueError(f"Invalid skill directory name: '{skill_dir_name}'")

        if skill_dir_name in BUILTIN_SKILLS:
            raise ValueError(
                f"'{skill_dir_name}' conflicts with a built-in skill"
            )
        if REGISTRY.get_spec(skill_dir_name) is not None:
            raise ValueError(f"Skill '{skill_dir_name}' is already registered")
        if (target_root / skill_dir_name).exists():
            raise ValueError(
                f"Skill '{skill_dir_name}' was already uploaded previously"
            )

        # Phase 3: validate.
        _validate_skill_dir(top_level_entries[0])

        # Phase 4: move the validated skill dir out of temp into the
        # persistent location. shutil.move handles cross-FS copy if
        # needed (we expect same FS in practice).
        dest = target_root / skill_dir_name
        shutil.move(str(top_level_entries[0]), str(dest))
        # The temp dir cleanup at the end of `with` is now a no-op for
        # the moved contents (they're not under tmp anymore).

    # Phase 5: hot-load. We add the persistent root to sys.path once
    # per session so subsequent importlib calls find uploaded skills.
    if str(target_root) not in sys.path:
        sys.path.insert(0, str(target_root))

    pre_count = len(REGISTRY.list_specs())
    try:
        _exec_skill_module(skill_dir_name, dest / f"{skill_dir_name}.py")
    except Exception as e:
        # Rollback the file move.
        shutil.rmtree(dest, ignore_errors=True)
        logger.exception("Uploaded skill '%s' raised on import", skill_dir_name)
        raise ValueError(f"Handler raised on import: {type(e).__name__}: {e}") from e

    post_count = len(REGISTRY.list_specs())
    if post_count == pre_count:
        # Module loaded fine but didn't call REGISTRY.register.
        shutil.rmtree(dest, ignore_errors=True)
        raise ValueError(
            "Handler module loaded but did not call REGISTRY.register(...). "
            "Make sure the file ends with REGISTRY.register(SPEC, handler)."
        )

    spec = REGISTRY.get_spec(skill_dir_name)
    if spec is None:
        # Belt-and-braces: importlib succeeded, REGISTRY grew, but
        # get_spec returned None. Shouldn't happen, but be defensive.
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError("Internal: registry state inconsistent after import")

    return {
        "name": skill_dir_name,
        "spec": spec.model_dump(),
        "enabled": REGISTRY.is_enabled(skill_dir_name),
        "uploaded": True,
    }


def uninstall_skill(name: str) -> None:
    """Remove an uploaded skill from REGISTRY and delete its directory.
    Built-in skills are refused."""
    if name in BUILTIN_SKILLS:
        raise ValueError(f"Cannot delete built-in skill '{name}'")
    if REGISTRY.get_spec(name) is None:
        raise ValueError(f"Skill '{name}' is not registered")
    REGISTRY.unregister(name)
    target = Path(get_settings().user_skills_dir) / name
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


def load_uploaded_skills_at_startup() -> int:
    """Re-import every previously uploaded skill so they survive
    container restarts. Returns the count successfully loaded."""
    settings = get_settings()
    root = Path(settings.user_skills_dir)
    if not root.exists():
        return 0
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
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
            # Already loaded (e.g. dev hot-reload). Skip.
            continue
        handler = entry / f"{entry.name}.py"
        if not handler.exists():
            logger.warning(
                "Uploaded skill '%s' missing handler file; skipping",
                entry.name,
            )
            continue
        try:
            _exec_skill_module(entry.name, handler)
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
    # Register in sys.modules BEFORE exec so the handler can do
    # `from user_test_skill import X` if it ever needs to.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        # If exec raised, drop the half-loaded module so a retry starts clean
        sys.modules.pop(name, None)
        raise
