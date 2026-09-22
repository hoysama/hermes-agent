"""czip-auto plugin — automatic verbatim archive of long sessions.

General plugin (NOT a memory provider, so it coexists with any configured
``memory.provider``): a best-effort ``on_session_end`` hook packs long
transcripts in the background, and three model tools (``czip_map`` /
``czip_search`` / ``czip_range``) let the model retrieve from packs on demand.
``/czip-auto`` is the manual fallback; daily use needs no slash and no MCP.

Invariants honored: hook returns fast (schedules a ``spawn_context_thread``,
never packs inline); pack failures never raise; home paths resolve per call
via ``get_hermes_home()`` and background state keys on ``hermes_home_key()``;
no new ``HERMES_*`` env vars (config lives under ``plugins.czip-auto``).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from . import packer

logger = logging.getLogger(__name__)

_TOOLSET = "czip"

_CRON_PLATFORMS = frozenset({"cron"})


def _active_home() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home()


def _maybe_pack(home_key: str, home_str: str, session_id: str) -> None:
    """Background pack job (runs under the spawner's contextvars)."""
    try:
        packer.run_pack_for_session(Path(home_str), session_id)
    except Exception as exc:
        logger.debug("czip-auto background pack failed: %s", exc)


def _on_session_end(
    session_id: str = "",
    platform: str = "",
    task_id: str = "",
    **_: Any,
) -> None:
    """Queue a background long-only pack check. Fast and never raises.

    Note: this hook fires per turn (turn_finalizer) plus at real boundaries
    (interrupt/shutdown/teardown) — the cooldown + growth gate in
    ``packer.should_pack`` is what keeps per-turn firings cheap: an index-file
    read decides the common skip path before any DB access.
    """
    try:
        if not session_id:
            return
        cfg = packer.load_config()
        if not cfg.get("enabled", True):
            return
        if (platform or "") in _CRON_PLATFORMS and not cfg.get("allow_cron", False):
            return
        if (platform or "") in packer._SKIP_PLATFORMS and platform != "cron":
            return
        from agent.memory_provider import spawn_context_thread
        from hermes_constants import hermes_home_key

        home = _active_home()
        key = hermes_home_key(home)
        thread = spawn_context_thread(
            _maybe_pack, name=f"czip-auto-pack-{session_id[:8]}",
            args=(key, str(home), session_id),
        )
        thread.start()
    except Exception as exc:
        logger.debug("czip-auto hook failed: %s", exc)


def _on_pre_compress(session_id: str = "", **_: Any) -> Optional[str]:
    """Contribute the verbatim-pack pointer line to the compression summary.

    Index-file read only (no DB): fires rarely, returns fast, never raises.
    None when this session has no pack yet — the summary prompt then stays
    byte-identical to the no-plugin behavior.
    """
    try:
        if not session_id:
            return None
        entry = packer.read_index(_active_home()).get(session_id)
        if not isinstance(entry, dict):
            return None
        pack_file = str(entry.get("file") or "").removesuffix(packer.PACK_SUFFIX)
        count = entry.get("msg_count", "?")
        if not pack_file:
            return None
        return (
            f"Verbatim transcript archived as pack {pack_file} ({count} msgs). "
            "Use czip_map/czip_search/czip_range to retrieve exact parts "
            "instead of relying only on this summary."
        )
    except Exception as exc:
        logger.debug("czip-auto pre_compress failed: %s", exc)
        return None


def _tool_home() -> Path:
    return _active_home()


def _handle_map(args: Dict[str, Any], **_: Any) -> str:
    pack = str(args.get("pack", ""))
    if not pack:
        return json.dumps({"success": False, "error": "pack is required"})
    try:
        return packer.map_pack(_tool_home(), pack)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)})


def _handle_search(args: Dict[str, Any], **_: Any) -> str:
    pack = str(args.get("pack", ""))
    query = str(args.get("query", ""))
    if not pack or not query:
        return json.dumps({"success": False, "error": "pack and query are required"})
    try:
        return packer.search_pack(_tool_home(), pack, query)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)})


def _handle_range(args: Dict[str, Any], **_: Any) -> str:
    pack = str(args.get("pack", ""))
    try:
        start = int(args.get("start", 0))
        end = int(args.get("end", start))
    except (TypeError, ValueError):
        return json.dumps({"success": False, "error": "start/end must be integers"})
    if not pack:
        return json.dumps({"success": False, "error": "pack is required"})
    try:
        return packer.range_pack(_tool_home(), pack, start, end)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)})


_MAP_SCHEMA = {
    "name": "czip_map",
    "description": "Show the small retrieval map of an archived session pack.",
    "parameters": {
        "type": "object",
        "properties": {"pack": {"type": "string", "description": "Pack id, session id/prefix, or file name."}},
        "required": ["pack"],
    },
}

_SEARCH_SCHEMA = {
    "name": "czip_search",
    "description": "Search inside an archived session pack for a query.",
    "parameters": {
        "type": "object",
        "properties": {
            "pack": {"type": "string", "description": "Pack id, session id/prefix, or file name."},
            "query": {"type": "string", "description": "Substring to find (case-insensitive)."},
        },
        "required": ["pack", "query"],
    },
}

_RANGE_SCHEMA = {
    "name": "czip_range",
    "description": "Read exact messages a-b from an archived session pack.",
    "parameters": {
        "type": "object",
        "properties": {
            "pack": {"type": "string", "description": "Pack id, session id/prefix, or file name."},
            "start": {"type": "integer", "description": "First message index (0-based)."},
            "end": {"type": "integer", "description": "Last message index (inclusive)."},
        },
        "required": ["pack", "start", "end"],
    },
}


def _handle_slash(raw_args: str) -> Optional[str]:
    argv = (raw_args or "").strip().split()
    home = _active_home()
    if not argv or argv[0] in {"help", "-h", "--help"}:
        return (
            "/czip-auto — automatic verbatim archive of long sessions\n\n"
            "Runs on its own via hooks; these are manual fallbacks:\n"
            "  status              Packs in this profile + current thresholds\n"
            "  pack <session_id>   Pack one session now (long-only gate applies)\n"
            "  list                Packs in this profile\n"
            "  map <pack>          Retrieval map of a pack\n"
            "  prune               Apply the retention caps now (max_packs / max_age_days)\n"
        )
    cmd = argv[0]
    if cmd == "status":
        cfg = packer.load_config()
        index = packer.read_index(home)
        return (
            f"packs: {len(index)} in {packer.pack_dir_for(home)} | "
            f"enabled={cfg.get('enabled')} min_messages={cfg.get('min_messages')} "
            f"min_bytes={cfg.get('min_bytes')} cooldown={cfg.get('cooldown_seconds')}s | "
            f"retention: max_packs={cfg.get('max_packs')} max_age_days={cfg.get('max_age_days')}"
        )
    if cmd == "list":
        index = packer.read_index(home)
        if not index:
            return "No packs in this profile yet."
        return "\n".join(
            f"{sid[:8]} | {e.get('msg_count', '?')} msgs | {e.get('file', '?')}"
            for sid, e in sorted(index.items()) if isinstance(e, dict)
        )
    if cmd == "pack":
        if len(argv) < 2:
            return "Usage: /czip-auto pack <session_id>"
        meta = packer.run_pack_for_session(home, argv[1])
        if meta is None:
            return "Skipped (short session, cooldown, or unknown session)."
        return f"Packed {meta['msg_count']} msgs -> {meta.get('file', '?')}."
    if cmd == "map":
        if len(argv) < 2:
            return "Usage: /czip-auto map <pack>"
        try:
            return packer.map_pack(home, argv[1])
        except Exception as exc:
            return f"pack read failed: {exc}"
    if cmd == "prune":
        stats = packer.prune_packs(home)
        return (
            f"pruned {stats['removed']} pack(s), removed {stats['orphans']} orphan file(s), "
            f"freed {stats['freed_bytes']} bytes"
        )
    return f"Unknown subcommand: {cmd}\n\n" + (_handle_slash("help") or "")


def register(ctx) -> None:
    ctx.register_hook("on_session_end", _on_session_end)
    ctx.register_hook("pre_compress", _on_pre_compress)
    ctx.register_tool(name="czip_map", toolset=_TOOLSET, schema=_MAP_SCHEMA, handler=_handle_map)
    ctx.register_tool(name="czip_search", toolset=_TOOLSET, schema=_SEARCH_SCHEMA, handler=_handle_search)
    ctx.register_tool(name="czip_range", toolset=_TOOLSET, schema=_RANGE_SCHEMA, handler=_handle_range)
    ctx.register_command("czip-auto", handler=_handle_slash,
                         description="Automatic verbatim archive of long sessions.")
    try:
        skill_path = Path(__file__).parent / "skill" / "SKILL.md"
        if skill_path.exists():
            ctx.register_skill("archive", skill_path,
                               description="Retrieve verbatim history from czip-auto packs.")
    except Exception as exc:
        logger.debug("czip-auto skill registration skipped: %s", exc)
