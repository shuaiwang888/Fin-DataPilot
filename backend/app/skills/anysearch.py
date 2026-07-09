"""Adapter for the anysearch-skill (bundled web + vertical search tool).

This is a thin async wrapper that shells out to the bundled Python CLI
(`<skill_dir>/scripts/anysearch_cli.py`). We pick Python because the
backend's venv already has `requests` (a project requirement), and the
Python CLI is the highest-priority runtime per the skill's own platform
detection rules.

The LLM-facing interface is a single `anysearch` tool with an `action`
enum. Each action maps to one CLI subcommand; the LLM fills the matching
args per the tool description.

Output handling: most actions return JSON (which we keep as a string in
`data` for the synthesizer to render); `extract` returns Markdown which
we also pass through as a string. A 50 KB cap protects against oversized
pages.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.skills.base import Handler, ToolParameter, ToolResult, ToolSpec
from app.skills.registry import REGISTRY
from app.utils.trace import generate_trace_id

logger = logging.getLogger(__name__)

SKILL_LOCAL_NAME = "anysearch"
SKILL_DISPLAY_NAME = "联网搜索（AnySearch）"
SKILL_VERSION = "2.1.0"
SKILL_CATEGORY = "search"

# Hard cap on returned stdout to keep a runaway extract() from blowing
# the chat message into MB-scale territory. 50 KB ~ 12k CJK chars.
MAX_OUTPUT_CHARS = 50_000

# The list of actions the LLM can pick. We use lowercase + underscore
# so the JSON args round-trip cleanly through tool_call payloads.
ACTIONS = ("search", "extract", "batch_search", "get_sub_domains")

# Public-facing description of what each action does (shown to the LLM
# in the tool description so it can pick the right one).
ACTION_HINTS = (
    "- action='search' 通用/垂直搜索。query 必填；可用 domain+sub_domain+sub_domain_params 走垂直。\n"
    "- action='extract' URL 正文提取（已转 Markdown）。url 必填。\n"
    "- action='batch_search' 并行批量。queries_json 必填（JSON 数组字符串，每项必须是 {query: ...} 对象；不要传字符串数组）。\n"
    "- action='get_sub_domains' 查某个 domain 的垂直子域能力。domain 必填。"
)

KNOWN_DOMAINS = {
    "general", "finance", "academic", "health", "legal", "ip", "code",
    "social_media", "travel", "film", "gaming", "business", "security",
    "energy", "environment", "agriculture", "resource",
}

# -- runtime detection / runtime.conf ---------------------------------


def _parse_runtime_conf(skill_dir: Path) -> tuple[str, str] | None:
    """Return (Runtime, Command) parsed from `<skill_dir>/runtime.conf`,
    or None if the file is missing / malformed / untrusted."""
    conf = skill_dir / "runtime.conf"
    if not conf.exists():
        return None
    try:
        runtime = ""
        command = ""
        for raw in conf.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("runtime:"):
                runtime = line.split(":", 1)[1].strip()
            elif line.lower().startswith("command:"):
                command = line.split(":", 1)[1].strip()
        if runtime and command:
            return (runtime, command)
    except OSError as exc:
        logger.warning("Failed to read runtime.conf at %s: %s", conf, exc)
    return None


def _detect_runtime(skill_dir: Path) -> tuple[str, str] | None:
    """Pick the first available runtime per the skill's documented
    priority order (Python > Node.js > Shell). Returns (label, argv0)
    where argv0 is the executable to invoke (without subcommand)."""
    py = shutil.which("python3") or shutil.which("python")
    if py:
        # Sanity check: --version should exit 0
        try:
            proc = __import__("subprocess").run(  # avoid shadowing the module import above
                [py, "--version"], capture_output=True, text=True, timeout=5
            )
            if proc.returncode == 0:
                cli = skill_dir / "scripts" / "anysearch_cli.py"
                if cli.exists():
                    return ("Python", f"{py} {cli}")
        except (OSError, __import__("subprocess").TimeoutExpired):  # noqa: PERF203
            pass

    node = shutil.which("node")
    if node:
        cli = skill_dir / "scripts" / "anysearch_cli.js"
        if cli.exists():
            return ("Node.js", f"{node} {cli}")

    # Shell CLIs: defer (Powershell on Windows only; bash requires jq+curl
    # which is harder to verify portably — most prod deploys pick Python).
    return None


def _write_runtime_conf(skill_dir: Path, runtime: str, command: str) -> None:
    """Persist the detected runtime so subsequent calls skip detection."""
    conf = skill_dir / "runtime.conf"
    try:
        conf.write_text(
            f"Runtime: {runtime}\nCommand: {command}\n",
            encoding="utf-8",
        )
        logger.info("Wrote runtime.conf → %s (%s)", conf, command)
    except OSError as exc:
        logger.warning("Failed to write runtime.conf: %s", exc)


def _resolve_command(skill_dir: Path) -> tuple[str, list[str]] | None:
    """Resolve the command to run. Returns (label, argv_prefix) where
    argv_prefix is the [executable, ...static_args] to prepend before
    the subcommand, or None if no runtime is available."""
    parsed = _parse_runtime_conf(skill_dir)
    if parsed:
        runtime, command = parsed
    else:
        detected = _detect_runtime(skill_dir)
        if not detected:
            return None
        runtime, command = detected
        _write_runtime_conf(skill_dir, runtime, command)

    # The runtime.conf Command may be a full shell-style string
    # (e.g. "python3 /path/cli.py") or just an executable. Tokenise on
    # whitespace; respect simple double-quoted segments.
    argv = _tokenise_command(command)
    if not argv:
        return None
    return (runtime, argv)


def _tokenise_command(cmd: str) -> list[str]:
    """Naive shell-like tokenizer: split on whitespace, respect double
    quotes. Sufficient for runtime.conf values like:
        python3 /abs/path/anysearch_cli.py
    """
    parts: list[str] = []
    buf = ""
    in_quote = False
    for ch in cmd:
        if ch == '"':
            in_quote = not in_quote
            continue
        if ch.isspace() and not in_quote:
            if buf:
                parts.append(buf)
                buf = ""
            continue
        buf += ch
    if buf:
        parts.append(buf)
    return parts


# -- output handling --------------------------------------------------


def _truncate(s: str, cap: int = MAX_OUTPUT_CHARS) -> str:
    if len(s) <= cap:
        return s
    return s[:cap] + f"\n\n…(已截断，原文 {len(s):,} chars)"


def _try_parse_json(s: str) -> Any:
    """Return parsed JSON if the string looks like JSON, else the raw
    string. Lets the synthesizer see structured data when possible."""
    stripped = s.strip()
    if not stripped:
        return s
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return s
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return s


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _coerce_max_results(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return max(1, min(n, 10))


def _normalise_domain_args(args: dict[str, Any]) -> tuple[str, str, str]:
    """Return cleaned (domain, sub_domain, sdp).

    LLMs occasionally put `finance` / `#finance` into sub_domain. That
    is a domain, not a sub_domain such as `finance.quote`; passing it
    as shared sub_domain can break or degrade the CLI call.
    """
    domain = _coerce_str(args.get("domain"))
    sub_domain = _coerce_str(args.get("sub_domain"))
    sdp = _coerce_str(args.get("sub_domain_params"))

    clean_sub = sub_domain.lstrip("#").strip()
    if clean_sub in KNOWN_DOMAINS and "." not in clean_sub:
        if not domain:
            domain = clean_sub
        sub_domain = ""

    return domain, sub_domain, sdp


def _normalise_batch_queries_json(args: dict[str, Any]) -> str | None:
    """Coerce batch_search queries into the CLI's object-array shape.

    Accepts:
      - JSON string: [{"query":"a"}]
      - JSON string accidentally produced by LLM: ["a", "b"]
      - Python list with either strings or objects
    """
    raw = args.get("queries_json")
    if raw in (None, ""):
        return None

    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
    else:
        parsed = raw

    if not isinstance(parsed, list) or not parsed:
        return None

    max_results = args.get("max_results")
    normalised: list[dict[str, Any]] = []
    for item in parsed[:5]:
        if isinstance(item, str):
            query = item.strip()
            if not query:
                continue
            obj: dict[str, Any] = {"query": query}
        elif isinstance(item, dict):
            query = _coerce_str(item.get("query"))
            if not query:
                continue
            obj = dict(item)
            obj["query"] = query
        else:
            continue

        obj_domain = _coerce_str(obj.get("domain"))
        obj_sub_domain = _coerce_str(obj.get("sub_domain"))
        obj_sdp = obj.get("sub_domain_params")
        clean_obj_sub = obj_sub_domain.lstrip("#").strip()
        if clean_obj_sub in KNOWN_DOMAINS and "." not in clean_obj_sub:
            if not obj_domain:
                obj["domain"] = clean_obj_sub
            obj.pop("sub_domain", None)

        if max_results not in (None, "") and not obj.get("max_results"):
            coerced_max = _coerce_max_results(max_results)
            if coerced_max is not None:
                obj["max_results"] = coerced_max
        normalised.append(obj)

    if not normalised:
        return None
    return json.dumps(normalised, ensure_ascii=False, separators=(",", ":"))


# -- action → argv builder -------------------------------------------


def _build_argv(action: str, args: dict[str, Any]) -> list[str] | None:
    """Translate LLM-facing args into CLI argv tokens. Returns None if
    the LLM failed to provide a required parameter (caller should turn
    that into a clear error)."""
    if action == "search":
        query = _coerce_str(args.get("query"))
        if not query:
            return None
        argv: list[str] = ["search", query]
        max_results = _coerce_max_results(args.get("max_results"))
        if max_results is not None:
            argv += ["--max_results", str(max_results)]
        domain, sub_domain, sdp = _normalise_domain_args(args)
        if domain:
            argv += ["--domain", domain]
        if sub_domain:
            argv += ["--sub_domain", sub_domain]
        if sdp:
            argv += ["--sdp", sdp]
        return argv

    if action == "extract":
        url = _coerce_str(args.get("url"))
        if not url:
            return None
        return ["extract", url]

    if action == "batch_search":
        # Two input shapes:
        #   1. shared params (query + domain + sub_domain + sdp) → repeat
        #      the query N times if max_queries is given, else the LLM
        #      must supply queries_json
        #   2. queries_json (a JSON array of {query, [domain, ...]})
        queries_json = _normalise_batch_queries_json(args)
        if queries_json:
            argv = ["batch_search", "--queries", queries_json]
        else:
            return None
        # Optional shared domain/sub_domain/sdp on top of per-item params.
        domain, sub_domain, sdp = _normalise_domain_args(args)
        if domain:
            argv += ["--domain", domain]
        if sub_domain:
            argv += ["--sub_domain", sub_domain]
        if sdp:
            argv += ["--sdp", sdp]
        return argv

    if action == "get_sub_domains":
        domain = _coerce_str(args.get("domain"))
        domains = _coerce_str(args.get("domains"))
        if domain:
            return ["get_sub_domains", "--domain", domain]
        if domains:
            return ["get_sub_domains", "--domains", domains]
        return None

    return None


# -- handler ----------------------------------------------------------


async def anysearch_handler(
    *,
    action: str,
    query: str = "",
    url: str = "",
    max_results: int | str = "",
    domain: str = "",
    sub_domain: str = "",
    sub_domain_params: str = "",
    queries_json: str = "",
    domains: str = "",
    api_key: str = "",
) -> ToolResult:
    settings = get_settings()
    skill_dir_str = settings.anysearch_dir
    if not skill_dir_str:
        return ToolResult(
            tool=SKILL_LOCAL_NAME,
            ok=False,
            error=(
                "anysearch-skill directory not found. Set ANYSEARCH_SKILL_DIR "
                "in .env or install the skill under Skills/anysearch-skill/."
            ),
        )
    skill_dir = Path(skill_dir_str)

    if action not in ACTIONS:
        return ToolResult(
            tool=SKILL_LOCAL_NAME,
            ok=False,
            error=f"action must be one of {list(ACTIONS)}; got {action!r}",
        )

    # Resolve the runtime + argv prefix (lazy; cached in runtime.conf).
    resolved = _resolve_command(skill_dir)
    if not resolved:
        return ToolResult(
            tool=SKILL_LOCAL_NAME,
            ok=False,
            error=(
                "No compatible runtime found for anysearch-skill. Need one of: "
                "Python 3.6+ (with `requests`), Node.js 12+, bash 3.2+ (with jq+curl), "
                "or PowerShell 5.1+."
            ),
        )
    runtime_label, base_argv = resolved
    trace_id = generate_trace_id()

    # Translate the LLM's tool_call args into CLI argv.
    args_dict: dict[str, Any] = {
        "query": query,
        "url": url,
        "max_results": max_results,
        "domain": domain,
        "sub_domain": sub_domain,
        "sub_domain_params": sub_domain_params,
        "queries_json": queries_json,
        "domains": domains,
    }
    try:
        sub_argv = _build_argv(action, args_dict)
    except Exception as exc:  # noqa: BLE001
        # Argv construction can raise on unexpected LLM-side input
        # (e.g. int("") before coerce, malformed dict, etc.). Don't
        # let that propagate out — a failed ToolResult lets the
        # reflector / loop guard decide the next step instead of
        # blowing up the whole agent graph.
        logger.exception("anysearch: argv build failed for action=%r", action)
        return ToolResult(
            tool=SKILL_LOCAL_NAME,
            ok=False,
            error=f"failed to build CLI argv for action={action!r}: {type(exc).__name__}: {exc}",
            trace_id=trace_id,
        )
    if sub_argv is None:
        return ToolResult(
            tool=SKILL_LOCAL_NAME,
            ok=False,
            error=f"action={action!r} requires a parameter that wasn't provided",
            trace_id=trace_id,
        )

    full_argv = [*base_argv, *sub_argv]

    # Pass an explicit API key (per-call) when caller provided one,
    # otherwise let the CLI pick it up from .env / env var / anonymous.
    env_overrides: dict[str, str] = {}
    key = (api_key or settings.anysearch_api_key).strip()
    if key:
        env_overrides["ANYSEARCH_API_KEY"] = key

    t0 = asyncio.get_event_loop().time()
    try:
        proc = await asyncio.create_subprocess_exec(
            *full_argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(skill_dir),
            env={**__import__("os").environ, **env_overrides},
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(),
                timeout=settings.anysearch_timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ToolResult(
                tool=SKILL_LOCAL_NAME,
                ok=False,
                error=f"anysearch CLI timeout after {settings.anysearch_timeout}s",
                trace_id=trace_id,
            )
    except FileNotFoundError as exc:
        return ToolResult(
            tool=SKILL_LOCAL_NAME,
            ok=False,
            error=f"anysearch CLI executable not found: {exc}",
            trace_id=trace_id,
        )
    except OSError as exc:
        return ToolResult(
            tool=SKILL_LOCAL_NAME,
            ok=False,
            error=f"Failed to spawn anysearch CLI: {exc}",
            trace_id=trace_id,
        )

    duration_ms = int((asyncio.get_event_loop().time() - t0) * 1000)
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        # CLI returns non-zero on backend errors. Surface stderr so the
        # synthesizer can see what went wrong (rate limit, auth, ...).
        return ToolResult(
            tool=SKILL_LOCAL_NAME,
            ok=False,
            error=(
                f"anysearch CLI exited with code {proc.returncode}. "
                f"stderr: {stderr.strip()[:500] or '(empty)'}"
            ),
            data={"stderr": stderr, "stdout": _truncate(stdout)},
            trace_id=trace_id,
            duration_ms=duration_ms,
        )

    # Happy path: hand the truncated output back. For search/batch_search
    # we attempt JSON parse so the LLM sees structured data; for extract
    # the output is already Markdown so we keep it as text.
    truncated = _truncate(stdout)
    if action in ("search", "batch_search", "get_sub_domains"):
        data: Any = _try_parse_json(truncated)
    else:
        data = truncated

    return ToolResult(
        tool=SKILL_LOCAL_NAME,
        ok=True,
        data=data,
        meta={
            "action": action,
            "runtime": runtime_label,
            "output_chars": len(stdout),
            "truncated": len(stdout) > MAX_OUTPUT_CHARS,
        },
        trace_id=trace_id,
        duration_ms=duration_ms,
    )


# -- spec / registration ---------------------------------------------


def _build_spec() -> ToolSpec:
    """Build the LLM-facing ToolSpec. Description includes the action
    hints so the LLM knows which args apply to which action."""
    return ToolSpec(
        name=SKILL_LOCAL_NAME,
        display_name=SKILL_DISPLAY_NAME,
        description=(
            "联网搜索 / 垂直领域搜索 / 批量并行搜索 / URL 正文提取工具。"
            "支持 finance、academic、health、legal、ip、code、social_media 等"
            "十几个垂直域；垂直搜索前可调 get_sub_domains 发现子域与必填参数。"
            "所有调用通过一个统一的 `action` 参数选择子命令：\n"
            f"{ACTION_HINTS}\n"
            "返回：search / batch_search / get_sub_domains 走 JSON-RPC，"
            "extract 返回 Markdown 文本。\n"
            "规则：\n"
            "  - 涉及金融/学术/医疗/法律/旅游/代码/知识产权等垂直领域时，"
            "先调 get_sub_domains 查子域；不调 get_sub_domains 直接 search 也能用，"
            "但走通用搜索效果差。\n"
            "  - sub_domain_params（--sdp）严格按 get_sub_domains 返回的 (required) 字段填，"
            "无值用空串占位（例：type=stock,symbol=AAPL,cn_code=）。\n"
            "  - max_results 取值 1-10。\n"
            "  - 当其他 Skill（financial-query / news-search / announcement-search / "
            "report-search）足以回答问题时优先用它们；本 Skill 用于：实时信息、"
            "事实核查、URL 正文、跨域综合搜索、社交媒体公开信息。"
        ),
        category=SKILL_CATEGORY,
        parameters=[
            ToolParameter(
                name="action",
                type="string",
                description="子命令：search / extract / batch_search / get_sub_domains",
                required=True,
                enum=list(ACTIONS),
            ),
            ToolParameter(
                name="query",
                type="string",
                description="[search] 自然语言查询词；必填",
                required=False,
            ),
            ToolParameter(
                name="url",
                type="string",
                description="[extract] 目标 URL（http/https）；必填",
                required=False,
            ),
            ToolParameter(
                name="max_results",
                type="integer",
                description="[search] 返回条数，1-10，默认 10",
                required=False,
            ),
            ToolParameter(
                name="domain",
                type="string",
                description=(
                    "[search / batch_search] 垂直域：general / finance / academic / "
                    "health / legal / ip / code / social_media / travel / film / gaming / "
                    "business / security / energy / environment / agriculture / resource"
                ),
                required=False,
            ),
            ToolParameter(
                name="sub_domain",
                type="string",
                description=(
                    "[search / batch_search] 垂直子域（先 get_sub_domains 查到再填；"
                    "例：finance.quote）"
                ),
                required=False,
            ),
            ToolParameter(
                name="sub_domain_params",
                type="string",
                description=(
                    "[search / batch_search] 垂直子域参数；格式 type=stock,symbol=AAPL,"
                    "cn_code= （KV 用逗号分隔）；也可填 JSON"
                ),
                required=False,
            ),
            ToolParameter(
                name="queries_json",
                type="string",
                description=(
                    "[batch_search] JSON 数组字符串，每项 {query, [domain, sub_domain, "
                    "sub_domain_params]}"
                ),
                required=False,
            ),
            ToolParameter(
                name="domains",
                type="string",
                description=(
                    "[get_sub_domains] 逗号分隔多个域，例 finance,health"
                ),
                required=False,
            ),
            ToolParameter(
                name="api_key",
                type="string",
                description=(
                    "可选：AnySearch API key（用于更高配额；不传则匿名 / 用 .env）"
                ),
                required=False,
            ),
        ],
        examples=[
            {
                "action": "search",
                "query": "宁德时代 2025 Q3 财报",
                "max_results": 5,
            },
            {
                "action": "search",
                "query": "AAPL",
                "domain": "finance",
                "sub_domain": "finance.quote",
                "sub_domain_params": "type=stock,symbol=AAPL,cn_code=",
                "max_results": 5,
            },
            {
                "action": "get_sub_domains",
                "domain": "finance",
            },
            {
                "action": "extract",
                "url": "https://example.com/article",
            },
        ],
        version=SKILL_VERSION,
    )


SPEC = _build_spec()

# Only register if the skill directory is actually present. This keeps
# local dev / CI green when the skill hasn't been cloned, and HF Space
# startup won't error out on the import.
_settings = get_settings()
if _settings.anysearch_dir:
    REGISTRY.register(SPEC, anysearch_handler)
    logger.info(
        "anysearch-skill registered (dir=%s, runtime=%s)",
        _settings.anysearch_dir,
        _parse_runtime_conf(Path(_settings.anysearch_dir))[0]
        if _parse_runtime_conf(Path(_settings.anysearch_dir))
        else "auto-detect",
    )
else:
    logger.warning(
        "anysearch-skill directory not found; skill NOT registered. "
        "Set ANYSEARCH_SKILL_DIR or place the skill at Skills/anysearch-skill/."
    )
