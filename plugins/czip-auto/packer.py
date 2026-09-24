"""czip-auto packer — stdlib-only verbatim session archive (CZAP1).

Local design inspired by the map+range retrieval idea: the pack file never
enters context; the model loads a small map, then pulls exact ranges on demand.

Format (original, not HKP1-compatible by intent — avoids any format claim):
    b"CZAP1" | meta_len (4B BE) | data_len (4B BE) | LZMA(meta) | LZMA(body)

Meta: session id, title, counters, pack timestamp. Body: message record list
(role, content) with exact-duplicate tool outputs replaced by [SAME-#N]
markers, resolved back to full text on read.

Stdlib only (json/struct/lzma/time). All Hermes I/O (SessionDB, config, home)
is resolved per call — never cached at import — so one process serving many
profiles never leaks state between them.
"""

from __future__ import annotations

import json
import lzma
import struct
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MAGIC = b"CZAP1"
VERSION = 1
PACK_SUFFIX = ".czap"
INDEX_NAME = "index.json"
PACK_DIR_NAME = "session-packs"

DEFAULTS = {
    "enabled": True,
    "min_messages": 50,
    "min_bytes": 100000,
    "cooldown_seconds": 3600,
    "min_growth_messages": 20,
    "allow_cron": False,
    # Retention: keeps the pack directory from growing without bound. ``0``
    # disables the respective cap. The newest pack always survives a pass.
    "max_packs": 0,
    "max_age_days": 0,
    "max_total_bytes": 5368709120,
}

# Platforms whose sessions are never auto-packed (internal workers, unless
# explicitly allowed via config). Cron sessions run with skip_memory=True by
# design; gateway_hygiene is the throwaway manual-compression agent.
_SKIP_PLATFORMS = frozenset({"cron", "gateway_hygiene"})


def load_config() -> Dict[str, Any]:
    """Config from config.yaml ``plugins.czip-auto`` over DEFAULTS (read-only)."""
    cfg = dict(DEFAULTS)
    try:
        from hermes_cli.config import load_config_readonly, cfg_get

        user = cfg_get(load_config_readonly(), "plugins", "czip-auto", default={}) or {}
        if isinstance(user, dict):
            for key in DEFAULTS:
                if key in user:
                    cfg[key] = user[key]
    except Exception:
        pass
    return cfg


def pack_dir_for(home: Path) -> Path:
    """Profile-scoped pack dir, resolved per call from the owning home."""
    return Path(home) / PACK_DIR_NAME


def _index_path(home: Path) -> Path:
    return pack_dir_for(home) / INDEX_NAME


def read_index(home: Path) -> Dict[str, Any]:
    try:
        raw = _index_path(home).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_index(home: Path, index: Dict[str, Any]) -> None:
    try:
        pack_dir_for(home).mkdir(parents=True, exist_ok=True)
        _index_path(home).write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _content_of(msg: Dict[str, Any]) -> str:
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except Exception:
        return str(content)


def _dedupe(records: List[Dict[str, str]]) -> Tuple[List[Dict[str, str]], int]:
    """Replace exact-duplicate tool outputs with [SAME-#N] markers.

    Returns (deduped_records, duplicate_count). First occurrence keeps full
    text; later identical tool outputs point at it by 0-based record index.
    """
    seen: Dict[str, int] = {}
    out: List[Dict[str, str]] = []
    dupes = 0
    for idx, rec in enumerate(records):
        if rec["role"] == "tool":
            key = rec["content"]
            if key and key in seen:
                out.append({"role": "tool", "content": f"[SAME-#{seen[key]}]"})
                dupes += 1
                continue
            if key:
                seen[key] = idx
        out.append(rec)
    return out, dupes


def should_pack(
    session_id: str,
    msg_count: int,
    raw_bytes: int,
    cfg: Dict[str, Any],
    index: Dict[str, Any],
    *,
    now: Optional[float] = None,
) -> bool:
    """Long-only gate with cooldown and growth requirements."""
    if not cfg.get("enabled", True):
        return False
    if msg_count < int(cfg.get("min_messages", 0)):
        return False
    if raw_bytes < int(cfg.get("min_bytes", 0)):
        return False
    entry = index.get(session_id)
    if not isinstance(entry, dict):
        return True
    try:
        packed_at = float(entry.get("packed_at", 0))
        packed_count = int(entry.get("msg_count", 0))
    except (TypeError, ValueError):
        return True
    now = time.time() if now is None else now
    if now - packed_at < float(cfg.get("cooldown_seconds", 0)):
        return False
    return (msg_count - packed_count) >= int(cfg.get("min_growth_messages", 0))


def pack_messages(
    session_id: str,
    title: str,
    messages: List[Dict[str, Any]],
    dest: Path,
) -> Dict[str, Any]:
    """Serialize + dedupe + compress messages into a CZAP1 pack file."""
    records = [{"role": str(m.get("role", "?")), "content": _content_of(m)} for m in messages]
    raw_bytes = sum(len(r["content"]) for r in records)
    deduped, dupes = _dedupe(records)
    meta = {
        "format": "CZAP1",
        "version": VERSION,
        "session_id": session_id,
        "title": title or "",
        "msg_count": len(records),
        "raw_bytes": raw_bytes,
        "duplicates": dupes,
        "packed_at": int(time.time()),
    }
    meta_blob = json.dumps(meta, ensure_ascii=False).encode("utf-8")
    body_blob = json.dumps(deduped, ensure_ascii=False).encode("utf-8")
    meta_z = lzma.compress(meta_blob, preset=6)
    body_z = lzma.compress(body_blob, preset=6)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as fh:
        fh.write(MAGIC)
        fh.write(struct.pack(">I", len(meta_z)))
        fh.write(struct.pack(">I", len(body_z)))
        fh.write(meta_z)
        fh.write(body_z)
    return meta


def read_pack(path: Path) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """Load a pack file; SAME markers are resolved back to full text."""
    with open(path, "rb") as fh:
        blob = fh.read()
    if blob[:5] != MAGIC:
        raise ValueError(f"not a CZAP1 pack: {path}")
    meta_len, data_len = struct.unpack(">II", blob[5:13])
    meta_z = blob[13:13 + meta_len]
    body_z = blob[13 + meta_len:13 + meta_len + data_len]
    meta = json.loads(lzma.decompress(meta_z).decode("utf-8"))
    records = json.loads(lzma.decompress(body_z).decode("utf-8"))
    resolved: List[Dict[str, str]] = []
    for rec in records:
        content = rec.get("content", "")
        if rec.get("role") == "tool" and isinstance(content, str) and content.startswith("[SAME-#"):
            try:
                ref = int(content[len("[SAME-#"):-1])
                content = resolved[ref]["content"] if 0 <= ref < len(resolved) else content
            except (ValueError, IndexError):
                pass
        resolved.append({"role": rec.get("role", "?"), "content": content})
    return meta, resolved


def _resolve_pack_file(home: Path, pack_id: str) -> Optional[Path]:
    """Pack id, session id/prefix, or file name -> pack path (profile-scoped)."""
    d = pack_dir_for(home)
    if not d.is_dir():
        return None
    cand = d / pack_id
    if cand.is_file():
        return cand
    if not pack_id.endswith(PACK_SUFFIX):
        cand = d / (pack_id + PACK_SUFFIX)
        if cand.is_file():
            return cand
    matches = sorted(d.glob(f"{pack_id}*{PACK_SUFFIX}"))
    if matches:
        return matches[0]
    index = read_index(home)
    for sid, entry in index.items():
        if isinstance(entry, dict) and (sid == pack_id or sid.startswith(pack_id)):
            p = d / str(entry.get("file", ""))
            if p.is_file():
                return p
    return None


def build_map(meta: Dict[str, Any], records: List[Dict[str, str]]) -> str:
    """Small RAG map (~1-2k tokens): counts + user requests + recent + instruction."""
    counts: Dict[str, int] = {}
    for r in records:
        counts[r["role"]] = counts.get(r["role"], 0) + 1
    lines = [
        f"pack {meta.get('session_id', '?')} | {meta.get('title', '')} | "
        f"{meta.get('msg_count', len(records))} msgs | {meta.get('raw_bytes', 0)} bytes",
        "roles: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
    ]
    shown = 0
    for i, r in enumerate(records):
        if r["role"] == "user" and r["content"].strip():
            lines.append(f"Q{i}: {r['content'][:120]}")
            shown += 1
            if shown >= 5:
                break
    lines.append("recent:")
    for i in range(max(0, len(records) - 5), len(records)):
        r = records[i]
        tag = {"user": "U", "assistant": "A", "tool": "T"}.get(r["role"], r["role"][:1])
        lines.append(f"{tag}{i}> {r['content'][:200]}")
    lines.append("Use czip_search <pack> <query> to find indices, then czip_range <pack> <a-b>.")
    return "\n".join(lines)


def map_pack(home: Path, pack_id: str) -> str:
    path = _resolve_pack_file(home, pack_id)
    if path is None:
        return json.dumps({"success": False, "error": f"pack not found: {pack_id}"})
    meta, records = read_pack(path)
    return build_map(meta, records)


def search_pack(home: Path, pack_id: Optional[str], query: str, *, limit: int = 20) -> str:
    q = (query or "").strip().lower()
    if not q:
        return json.dumps({"success": False, "error": "query is required"})

    # Specific pack search
    if pack_id and str(pack_id).strip() not in {"all", "*", ""}:
        path = _resolve_pack_file(home, str(pack_id).strip())
        if path is None:
            return json.dumps({"success": False, "error": f"pack not found: {pack_id}"})
        _, records = read_pack(path)
        pid = path.stem
        hits = []
        for i, r in enumerate(records):
            if q in r["content"].lower():
                hits.append({"pack": pid, "index": i, "role": r["role"], "snippet": r["content"][:200]})
                if len(hits) >= limit:
                    break
        return json.dumps({"success": True, "query": query, "pack": pid, "hits": hits}, ensure_ascii=False)

    # Multi-pack search across all available session packs
    d = pack_dir_for(home)
    if not d.is_dir():
        return json.dumps({"success": True, "query": query, "hits": [], "total_packs_searched": 0})

    index = read_index(home)
    pack_files: List[Path] = []
    if index:
        for sid, entry in sorted(index.items(), key=lambda kv: kv[1].get("packed_at", 0) if isinstance(kv[1], dict) else 0, reverse=True):
            if isinstance(entry, dict) and entry.get("file"):
                p = d / str(entry["file"])
                if p.is_file() and p not in pack_files:
                    pack_files.append(p)
    for p in sorted(d.glob(f"*{PACK_SUFFIX}"), key=lambda x: x.stat().st_mtime, reverse=True):
        if p not in pack_files:
            pack_files.append(p)

    hits = []
    for path in pack_files:
        try:
            meta, records = read_pack(path)
            pid = path.stem
            for i, r in enumerate(records):
                if q in r["content"].lower():
                    hits.append({
                        "pack": pid,
                        "session_id": meta.get("session_id", ""),
                        "index": i,
                        "role": r["role"],
                        "snippet": r["content"][:200],
                    })
                    if len(hits) >= limit:
                        break
        except Exception:
            continue
        if len(hits) >= limit:
            break

    return json.dumps({"success": True, "query": query, "hits": hits, "total_packs_searched": len(pack_files)}, ensure_ascii=False)


def range_pack(home: Path, pack_id: str, start: int, end: int) -> str:
    path = _resolve_pack_file(home, pack_id)
    if path is None:
        return json.dumps({"success": False, "error": f"pack not found: {pack_id}"})
    _, records = read_pack(path)
    start = max(0, start)
    end = min(len(records) - 1, end)
    if start > end:
        return json.dumps({"success": False, "error": "empty range"})
    return json.dumps(
        {"success": True, "messages": records[start:end + 1]}, ensure_ascii=False
    )


def prune_packs(
    home: Path,
    cfg: Optional[Dict[str, Any]] = None,
    *,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Drop packs past the retention caps: ``{"removed", "freed_bytes", "orphans"}``.

    Newest-first, so the single newest pack survives even when it alone exceeds
    the byte budget — a profile must never end up with no archive. Also sweeps
    ``.czap`` files no surviving index entry points at (a crashed pack, or an
    index rewritten outside this module). Never raises: hook-path code.
    """
    stats: Dict[str, Any] = {"removed": 0, "freed_bytes": 0, "orphans": 0}
    try:
        cfg = cfg if cfg is not None else load_config()
        d = pack_dir_for(home)
        if not d.is_dir():
            return stats
        now = time.time() if now is None else now
        max_packs = int(cfg.get("max_packs") or 0)
        max_age_days = float(cfg.get("max_age_days") or 0)
        max_bytes = int(cfg.get("max_total_bytes") or 0)

        index = read_index(home)
        entries = [(sid, e) for sid, e in index.items() if isinstance(e, dict)]
        entries.sort(key=lambda kv: float(kv[1].get("packed_at") or 0), reverse=True)

        kept: Dict[str, Any] = {}
        kept_bytes = 0
        for sid, entry in entries:
            path = d / str(entry.get("file") or "")
            size = path.stat().st_size if path.is_file() else 0
            age_days = (now - float(entry.get("packed_at") or 0)) / 86400.0
            expired = (
                (bool(max_packs) and len(kept) >= max_packs)
                or (bool(max_age_days) and age_days > max_age_days)
                or (bool(max_bytes) and kept and kept_bytes + size > max_bytes)
            )
            if expired:
                if path.is_file():
                    try:
                        path.unlink()
                    except Exception:
                        kept[sid] = entry  # still on disk: stay consistent with the index
                        kept_bytes += size
                        continue
                    stats["freed_bytes"] += size
                stats["removed"] += 1
                continue
            kept[sid] = entry
            kept_bytes += size

        referenced = {str(e.get("file") or "") for e in kept.values()}
        for orphan in d.glob(f"*{PACK_SUFFIX}"):
            if orphan.name in referenced:
                continue
            try:
                orphan.unlink()
                stats["orphans"] += 1
            except Exception:
                continue

        if len(kept) != len(index):
            _write_index(home, kept)
    except Exception:
        return stats
    return stats


def run_pack_for_session(home: Path, session_id: str, *, force: bool = False) -> Optional[Dict[str, Any]]:
    """Load transcript (read-only) and pack it when the long-only gate passes.

    Returns the pack meta on success, None when skipped. Never raises — every
    failure path returns None so hooks never break a turn.
    """
    try:
        cfg = load_config()
        if not cfg.get("enabled", True) or not session_id:
            return None
        index = read_index(home)
        entry = index.get(session_id)
        if not force and isinstance(entry, dict):
            try:
                if time.time() - float(entry.get("packed_at", 0)) < float(cfg.get("cooldown_seconds", 0)):
                    return None
            except (TypeError, ValueError):
                pass
        from hermes_state import SessionDB

        db_path = Path(home) / "state.db"
        if not db_path.exists():
            return None
        db = SessionDB(db_path=db_path, read_only=True)
        try:
            session = db.get_session(session_id)
            if session is None:
                return None
            if not force and str(session.get("source") or "").lower() == "subagent":
                # Delegated children are covered by the parent's transcript,
                # which packs on its own growth gate. Archiving each child
                # separately would litter one pack per delegation.
                return None
            messages = db.get_messages(session_id)
        finally:
            try:
                db.close()
            except Exception:
                pass
        if not messages:
            return None
        raw_bytes = sum(len(_content_of(m)) for m in messages)
        if not force and not should_pack(session_id, len(messages), raw_bytes, cfg, index):
            return None
        title = ""
        try:
            title = str(session.get("title") or "")
        except Exception:
            pass
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        dest = pack_dir_for(home) / f"{session_id[:8]}-{stamp}{PACK_SUFFIX}"
        meta = pack_messages(session_id, title, messages, dest)
        meta["file"] = dest.name
        old_file = entry.get("file") if isinstance(entry, dict) else None
        index[session_id] = meta
        _write_index(home, index)
        if old_file and old_file != dest.name:
            # A repack supersedes the previous snapshot completely (the new
            # pack holds the full transcript), so remove the orphaned file —
            # otherwise every growth-triggered repack litters the directory
            # and the index drifts out of sync with it.
            try:
                (pack_dir_for(home) / str(old_file)).unlink()
            except Exception:
                pass
        prune_packs(home, cfg)
        return meta
    except Exception:
        return None
