"""Tests for the czip-auto plugin (automatic verbatim archive of long sessions).

Behavior contracts (not snapshots):
  * pack -> read round-trip is bit-exact, including SAME-marker resolution.
  * exact-duplicate tool outputs are eliminated with a reported count.
  * the long-only gate skips short sessions, packs long ones, honors
    cooldown, and re-packs after real growth.
  * map/search/range relate correctly to the packed transcript.
  * packs are profile-scoped: home A never sees home B's packs.
  * the session-end hook never raises and returns fast (schedules, not packs).
"""

import importlib.util
import json
import sys
import time
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "plugins" / "czip-auto"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def packer():
    return _load_module("czip_auto_packer_under_test", PLUGIN_DIR / "packer.py")


@pytest.fixture(scope="module")
def plugin(packer):
    pkg = types.ModuleType("czip_auto_plugin_under_test")
    pkg.__path__ = [str(PLUGIN_DIR)]
    sys.modules["czip_auto_plugin_under_test"] = pkg
    init = _load_module("czip_auto_plugin_under_test.__init__", PLUGIN_DIR / "__init__.py")
    init.packer = packer
    return init


@pytest.fixture()
def home_a(tmp_path, monkeypatch):
    home = tmp_path / "homeA" / ".hermes"
    home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _msgs(n, dup_tool_payload="x" * 3000):
    out = []
    for i in range(n):
        out.append({"role": "user", "content": f"question number {i} about python jobs"})
        out.append({"role": "assistant", "content": f"answer {i}"})
        out.append({"role": "tool", "content": dup_tool_payload})
    return out


# ---------------------------------------------------------------------------
# Round-trip + dedupe contracts
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_pack_read_is_bit_exact(self, packer, tmp_path):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
            {"role": "tool", "content": "output blob"},
        ]
        dest = tmp_path / "s-20240101.czap"
        packer.pack_messages("sess1", "t", msgs, dest)
        meta, records = packer.read_pack(dest)
        assert [r["content"] for r in records] == [m["content"] for m in msgs]
        assert meta["msg_count"] == 3

    def test_duplicate_tool_outputs_eliminated_and_resolved(self, packer, tmp_path):
        blob = "SAME-BIG-OUTPUT-" * 500
        msgs = [
            {"role": "tool", "content": blob},
            {"role": "user", "content": "next"},
            {"role": "tool", "content": blob},
            {"role": "tool", "content": blob},
        ]
        dest = tmp_path / "d.czap"
        meta = packer.pack_messages("s", "", msgs, dest)
        assert meta["duplicates"] == 2
        assert dest.stat().st_size < len(blob) * 3
        _, records = packer.read_pack(dest)
        assert [r["content"] for r in records] == [m["content"] for m in msgs]

    def test_non_tool_duplicates_are_kept(self, packer, tmp_path):
        msgs = [{"role": "user", "content": "same text twice"},
                {"role": "user", "content": "same text twice"}]
        dest = tmp_path / "u.czap"
        meta = packer.pack_messages("s", "", msgs, dest)
        assert meta["duplicates"] == 0


# ---------------------------------------------------------------------------
# Long-only gate contract
# ---------------------------------------------------------------------------

class TestShouldPack:
    def _cfg(self, packer, **over):
        cfg = dict(packer.DEFAULTS)
        cfg.update(over)
        return cfg

    def test_short_session_skipped(self, packer):
        assert packer.should_pack("s", 10, 10_000, self._cfg(packer), {}) is False

    def test_small_bytes_skipped(self, packer):
        assert packer.should_pack("s", 500, 100, self._cfg(packer), {}) is False

    def test_long_session_packs(self, packer):
        assert packer.should_pack("s", 300, 600_000, self._cfg(packer), {}) is True

    def test_cooldown_blocks_immediate_repack(self, packer):
        index = {"s": {"packed_at": time.time(), "msg_count": 300}}
        assert packer.should_pack("s", 310, 700_000, self._cfg(packer), index) is False

    def test_growth_after_cooldown_repacks(self, packer):
        index = {"s": {"packed_at": time.time() - 7200, "msg_count": 300}}
        cfg = self._cfg(packer)
        assert packer.should_pack("s", 310, 700_000, cfg, index) is False
        assert packer.should_pack("s", 400, 900_000, cfg, index) is True

    def test_disabled_never_packs(self, packer):
        assert packer.should_pack("s", 9999, 99_999_999,
                                  self._cfg(packer, enabled=False), {}) is False


# ---------------------------------------------------------------------------
# Retrieval contracts: map/search/range relate to the transcript
# ---------------------------------------------------------------------------

class TestRetrieval:
    def _pack(self, packer, home):
        msgs = _msgs(8)
        msgs.insert(3, {"role": "user", "content": "offer from Acme Corp salary details"})
        dest = packer.pack_dir_for(home) / "sess-20240101.czap"
        packer.pack_messages("sess-abc123", "job hunt", msgs, dest)
        return dest, msgs

    def test_map_mentions_counts_and_recent(self, packer, home_a):
        dest, msgs = self._pack(packer, home_a)
        out = packer.map_pack(home_a, dest.name)
        assert "24" in out  # 8*3 transcript rows packed
        assert "recent:" in out
        assert "czip_search" in out

    def test_search_finds_index_range_reads_it_back(self, packer, home_a):
        dest, msgs = self._pack(packer, home_a)
        found = json.loads(packer.search_pack(home_a, dest.name, "acme"))
        assert found["success"] is True
        assert len(found["hits"]) == 1
        idx = found["hits"][0]["index"]
        back = json.loads(packer.range_pack(home_a, dest.name, idx, idx))
        assert back["success"] is True
        assert "Acme Corp" in back["messages"][0]["content"]

    def test_unknown_pack_is_a_clean_error(self, packer, home_a):
        assert json.loads(packer.search_pack(home_a, "nope", "x"))["success"] is False
        assert json.loads(packer.range_pack(home_a, "nope", 0, 1))["success"] is False

    def test_search_multi_pack_when_pack_is_omitted(self, packer, home_a):
        dest, msgs = self._pack(packer, home_a)
        found = json.loads(packer.search_pack(home_a, None, "acme"))
        assert found["success"] is True
        assert len(found["hits"]) == 1
        assert found["hits"][0]["pack"] == dest.stem
        found_all = json.loads(packer.search_pack(home_a, "all", "acme"))
        assert found_all["success"] is True
        assert len(found_all["hits"]) == 1


# ---------------------------------------------------------------------------
# Retention caps: the pack directory must not grow without bound
# ---------------------------------------------------------------------------

class TestRetention:
    def _pack(self, packer, home, sid, packed_at=None, msgs=None):
        dest = packer.pack_dir_for(home) / f"{sid}-20240101{packer.PACK_SUFFIX}"
        meta = packer.pack_messages(sid, "", msgs or _msgs(2), dest)
        index = packer.read_index(home)
        index[sid] = {**meta, "file": dest.name,
                      "packed_at": time.time() if packed_at is None else packed_at}
        packer._write_index(home, index)
        return dest

    def test_max_packs_keeps_newest(self, packer, home_a):
        now = time.time()
        files = [self._pack(packer, home_a, f"s{i}", packed_at=now - 100 + i) for i in range(4)]
        stats = packer.prune_packs(
            home_a, {"max_packs": 2, "max_age_days": 0, "max_total_bytes": 0}, now=now)
        assert stats["removed"] == 2
        assert sorted(f.name for f in packer.pack_dir_for(home_a).glob("*.czap")) == \
            sorted([files[3].name, files[2].name])
        assert set(packer.read_index(home_a)) == {"s2", "s3"}

    def test_max_age_drops_only_old_packs(self, packer, home_a):
        now = time.time()
        old = self._pack(packer, home_a, "old", packed_at=now - 10 * 86400)
        fresh = self._pack(packer, home_a, "fresh", packed_at=now - 3600)
        stats = packer.prune_packs(
            home_a, {"max_packs": 0, "max_age_days": 7, "max_total_bytes": 0}, now=now)
        assert stats["removed"] == 1
        assert not old.exists() and fresh.exists()
        assert set(packer.read_index(home_a)) == {"fresh"}

    def test_byte_budget_drops_oldest_first(self, packer, home_a):
        now = time.time()
        oldest = self._pack(packer, home_a, "a", packed_at=now - 200)
        middle = self._pack(packer, home_a, "b", packed_at=now - 100)
        newest = self._pack(packer, home_a, "c", packed_at=now)
        budget = newest.stat().st_size + middle.stat().st_size
        stats = packer.prune_packs(
            home_a, {"max_packs": 0, "max_age_days": 0, "max_total_bytes": budget}, now=now)
        assert stats["removed"] == 1
        assert newest.exists() and middle.exists() and not oldest.exists()

    def test_single_oversized_pack_always_survives(self, packer, home_a):
        only = self._pack(packer, home_a, "only")
        stats = packer.prune_packs(
            home_a, {"max_packs": 0, "max_age_days": 0, "max_total_bytes": 1})
        assert stats["removed"] == 0
        assert only.exists()

    def test_orphan_sweep_and_missing_dir_is_safe(self, packer, home_a):
        assert packer.prune_packs(home_a, {"max_packs": 1}) == \
            {"removed": 0, "freed_bytes": 0, "orphans": 0}
        keep = self._pack(packer, home_a, "keep")
        orphan = packer.pack_dir_for(home_a) / "orphan-20240101.czap"
        packer.pack_messages("orphan", "", [{"role": "user", "content": "x"}], orphan)
        stats = packer.prune_packs(
            home_a, {"max_packs": 10, "max_age_days": 0, "max_total_bytes": 0})
        assert stats["orphans"] == 1
        assert not orphan.exists() and keep.exists()

    def test_packing_prunes_to_the_cap(self, packer, home_a, monkeypatch):
        """End-to-end: a second session's pack evicts the first under max_packs=1."""
        from hermes_state import SessionDB

        cfg = {"enabled": True, "min_messages": 1, "min_bytes": 1,
               "cooldown_seconds": 0, "min_growth_messages": 0,
               "allow_cron": False, "max_packs": 1, "max_age_days": 0,
               "max_total_bytes": 0}
        monkeypatch.setattr(packer, "load_config", lambda: cfg)
        db = SessionDB(db_path=home_a / "state.db")
        try:
            db.create_session("retain-1", "test")
            db.create_session("retain-2", "test")
            db.replace_messages("retain-1", _msgs(2))
            db.replace_messages("retain-2", _msgs(2))
        finally:
            try:
                db.close()
            except Exception:
                pass
        first = packer.run_pack_for_session(home_a, "retain-1")
        assert first is not None
        import time as _time
        _time.sleep(1.1)  # packed_at has second granularity
        second = packer.run_pack_for_session(home_a, "retain-2")
        assert second is not None
        pack_dir = packer.pack_dir_for(home_a)
        assert [f.name for f in pack_dir.glob("*.czap")] == [second["file"]]
        assert set(packer.read_index(home_a)) == {"retain-2"}


# ---------------------------------------------------------------------------
# Profile isolation + hook safety + end-to-end SessionDB pack
# ---------------------------------------------------------------------------

class TestIsolationAndHook:
    def test_home_b_cannot_see_home_a_packs(self, packer, home_a, tmp_path):
        home_b = tmp_path / "homeB" / ".hermes"
        home_b.mkdir(parents=True)
        dest = packer.pack_dir_for(home_a) / "sess-x.czap"
        packer.pack_messages("sess-x", "", [{"role": "user", "content": "secret"}], dest)
        packer._write_index(home_a, {"sess-x": {"file": dest.name, "msg_count": 1,
                                                "packed_at": time.time()}})
        assert packer._resolve_pack_file(home_b, "sess") is None
        assert packer._resolve_pack_file(home_a, "sess") == dest

    def test_hook_never_raises_and_returns_fast(self, plugin, home_a):
        start = time.time()
        plugin._on_session_end(session_id="", platform="cli")
        plugin._on_session_end(session_id="missing", platform="cron")
        plugin._on_session_end(session_id="missing", platform="cli")
        assert time.time() - start < 2.0

    def test_run_pack_end_to_end_with_real_session_db(self, packer, home_a, monkeypatch):
        from hermes_state import SessionDB

        sid = "test-session-0001"
        db = SessionDB(db_path=home_a / "state.db")
        try:
            db.create_session(sid, "test")
            db.replace_messages(sid, _msgs(120))
        finally:
            try:
                db.close()
            except Exception:
                pass
        monkeypatch.setattr(packer, "load_config",
                            lambda: {"enabled": True, "min_messages": 100, "min_bytes": 1000,
                                     "cooldown_seconds": 3600, "min_growth_messages": 50,
                                     "allow_cron": False})
        meta = packer.run_pack_for_session(home_a, sid)
        assert meta is not None
        assert meta["msg_count"] == 360
        back_meta, records = packer.read_pack(packer.pack_dir_for(home_a) / meta["file"])
        assert back_meta["msg_count"] == 360
        assert records[0]["content"].startswith("question number 0")

    def test_run_pack_unknown_session_returns_none(self, packer, home_a, monkeypatch):
        monkeypatch.setattr(packer, "load_config",
                            lambda: {"enabled": True, "min_messages": 1, "min_bytes": 1,
                                     "cooldown_seconds": 0, "min_growth_messages": 0,
                                     "allow_cron": False})
        assert packer.run_pack_for_session(home_a, "does-not-exist") is None

    def test_run_pack_skips_subagent_children(self, packer, home_a, monkeypatch):
        """Delegated children (source='subagent') are never packed on their own;
        the parent transcript covers them (regression: 3 child packs on Modal)."""
        from hermes_state import SessionDB

        sid = "child-session-0001"
        db = SessionDB(db_path=home_a / "state.db")
        try:
            db.create_session(sid, "subagent")
            db.replace_messages(sid, _msgs(120))
        finally:
            try:
                db.close()
            except Exception:
                pass
        monkeypatch.setattr(packer, "load_config",
                            lambda: {"enabled": True, "min_messages": 1, "min_bytes": 1,
                                     "cooldown_seconds": 0, "min_growth_messages": 0,
                                     "allow_cron": False})
        assert packer.run_pack_for_session(home_a, sid) is None

    def test_repack_deletes_superseded_file(self, packer, home_a, monkeypatch):
        """A growth-triggered repack removes the previous snapshot so the
        directory never drifts out of sync with index.json (audit finding:
        orphan .czap files accumulated on every repack)."""
        from hermes_state import SessionDB

        sid = "orphan-session-0001"
        cfg = {"enabled": True, "min_messages": 1, "min_bytes": 1,
               "cooldown_seconds": 0, "min_growth_messages": 0,
               "allow_cron": False}
        monkeypatch.setattr(packer, "load_config", lambda: cfg)
        db = SessionDB(db_path=home_a / "state.db")
        try:
            db.create_session(sid, "test")
            db.replace_messages(sid, _msgs(10))
        finally:
            try:
                db.close()
            except Exception:
                pass
        first = packer.run_pack_for_session(home_a, sid)
        assert first is not None
        import time
        time.sleep(1.1)
        db = SessionDB(db_path=home_a / "state.db")
        try:
            db.replace_messages(sid, _msgs(11))
        finally:
            try:
                db.close()
            except Exception:
                pass
        second = packer.run_pack_for_session(home_a, sid)
        assert second is not None
        assert second["file"] != first["file"]
        pack_dir = packer.pack_dir_for(home_a)
        assert not (pack_dir / first["file"]).exists()
        assert (pack_dir / second["file"]).exists()
        leftovers = [f.name for f in pack_dir.glob("*.czap")]
        assert leftovers == [second["file"]]


# ---------------------------------------------------------------------------
# Bundled discovery (opt-in, like every general plugin)
# ---------------------------------------------------------------------------

class TestBundledDiscovery:
    def _mgr(self, home, enabled):
        import yaml
        (home / "config.yaml").write_text(
            yaml.safe_dump({"plugins": {"enabled": list(enabled)}}))
        from hermes_cli import plugins as pmod
        mgr = pmod.PluginManager()
        mgr.discover_and_load()
        return mgr

    def test_discovered_but_not_loaded_by_default(self, home_a):
        mgr = self._mgr(home_a, [])
        assert "czip-auto" in mgr._plugins
        assert mgr._plugins["czip-auto"].manifest.source == "bundled"
        assert not mgr._plugins["czip-auto"].enabled

    def test_opt_in_registers_hook_tools_command_and_skill(self, home_a):
        mgr = self._mgr(home_a, ["czip-auto"])
        assert mgr._plugins["czip-auto"].enabled
        assert len(mgr._hooks.get("on_session_end", [])) == 1
        assert len(mgr._hooks.get("pre_compress", [])) == 1
        assert {"czip_map", "czip_search", "czip_range"} <= set(mgr._plugin_tool_names)
        assert "czip-auto" in mgr._plugin_commands
        assert "czip-auto:archive" in mgr._plugin_skills


# ---------------------------------------------------------------------------
# pre_compress pointer line (v2-a): summary references the verbatim pack
# ---------------------------------------------------------------------------

class TestPreCompressPointer:
    def _write_entry(self, packer, home, sid="sess-1", fname="sess-1-20240101.czap"):
        packer._write_index(home, {sid: {"file": fname, "msg_count": 42,
                                         "packed_at": 1700000000}})

    def test_pointer_line_when_pack_exists(self, packer, plugin, home_a, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(home_a))
        self._write_entry(packer, home_a)
        line = plugin._on_pre_compress(session_id="sess-1")
        assert line is not None
        assert "sess-1-20240101" in line
        assert "42" in line
        assert "czip_map" in line

    def test_no_contribution_without_pack(self, packer, plugin, home_a, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(home_a))
        assert plugin._on_pre_compress(session_id="unknown") is None
        assert plugin._on_pre_compress(session_id="") is None

    def test_plugin_context_joins_and_defaults_empty(self, home_a, monkeypatch):
        from agent import conversation_compression as cc

        import hermes_cli.lifecycle as lc
        monkeypatch.setattr(lc, "invoke_hook", lambda name, **kw: ["", None, "  hello  "])
        agent = type("A", (), {"session_id": "s"})()
        assert cc._pre_compress_plugin_context(agent) == "hello"
        monkeypatch.setattr(lc, "invoke_hook", lambda name, **kw: [])
        assert cc._pre_compress_plugin_context(agent) == ""

    def test_plugin_context_never_raises(self, home_a, monkeypatch):
        from agent import conversation_compression as cc

        import hermes_cli.lifecycle as lc

        def boom(name, **kw):
            raise RuntimeError("hook exploded")

        monkeypatch.setattr(lc, "invoke_hook", boom)
        agent = type("A", (), {"session_id": "s"})()
        assert cc._pre_compress_plugin_context(agent) == ""
