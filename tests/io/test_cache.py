from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

from taze.io.cache import RegistryCache, cache_path, load_cache, save_cache


if TYPE_CHECKING:
    from pathlib import Path


def _use_tmp_cache(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))


class TestCachePath:
    def test_uses_xdg_cache_home(self, monkeypatch, tmp_path: Path) -> None:
        _use_tmp_cache(monkeypatch, tmp_path)
        assert cache_path() == tmp_path / "taze" / "pypi.json"


class TestSaveAndLoadCache:
    def test_round_trips_nested_data(self, monkeypatch, tmp_path: Path) -> None:
        _use_tmp_cache(monkeypatch, tmp_path)
        cache = {"requests": {"info": {"version": "2.0.0"}, "releases": {"2.0.0": [{"yanked": False}]}}}
        save_cache(cache)
        assert load_cache() == cache

    def test_save_skips_empty_cache(self, monkeypatch, tmp_path: Path) -> None:
        _use_tmp_cache(monkeypatch, tmp_path)
        save_cache({})
        assert not cache_path().exists()

    def test_load_missing_file_returns_empty(self, monkeypatch, tmp_path: Path) -> None:
        _use_tmp_cache(monkeypatch, tmp_path)
        assert load_cache() == {}

    def test_load_force_ignores_existing_cache(self, monkeypatch, tmp_path: Path) -> None:
        _use_tmp_cache(monkeypatch, tmp_path)
        save_cache({"requests": {"info": {}}})
        assert load_cache(force=True) == {}

    def test_load_expired_legacy_cache_returns_empty(self, monkeypatch, tmp_path: Path) -> None:
        _use_tmp_cache(monkeypatch, tmp_path)
        path = cache_path()
        path.parent.mkdir(parents=True)
        path.write_bytes(b'{"requests": {"info": {}}}')
        old = time.time() - 3600
        os.utime(path, (old, old))
        assert load_cache() == {}

    def test_load_corrupt_json_returns_empty(self, monkeypatch, tmp_path: Path) -> None:
        _use_tmp_cache(monkeypatch, tmp_path)
        path = cache_path()
        path.parent.mkdir(parents=True)
        path.write_bytes(b"not json")
        assert load_cache() == {}

    def test_load_non_dict_json_returns_empty(self, monkeypatch, tmp_path: Path) -> None:
        _use_tmp_cache(monkeypatch, tmp_path)
        path = cache_path()
        path.parent.mkdir(parents=True)
        path.write_bytes(b"[1, 2, 3]")
        assert load_cache() == {}

    def test_load_legacy_format_uses_file_mtime(self, monkeypatch, tmp_path: Path) -> None:
        _use_tmp_cache(monkeypatch, tmp_path)
        path = cache_path()
        path.parent.mkdir(parents=True)
        path.write_bytes(b'{"requests": {"info": {"version": "1.0"}}}')
        assert load_cache() == {"requests": {"info": {"version": "1.0"}}}

    def test_entries_expire_individually(self, monkeypatch, tmp_path: Path) -> None:
        _use_tmp_cache(monkeypatch, tmp_path)
        cache = RegistryCache()
        cache["stale"] = {"info": {"version": "1.0"}}
        cache.fetched_at["stale"] = time.time() - 3600
        cache["fresh"] = {"info": {"version": "2.0"}}
        save_cache(cache)
        loaded = load_cache()
        assert loaded == {"fresh": {"info": {"version": "2.0"}}}
        assert loaded.fetched_at["fresh"] == cache.fetched_at["fresh"]

    def test_saving_keeps_original_timestamps(self, monkeypatch, tmp_path: Path) -> None:
        _use_tmp_cache(monkeypatch, tmp_path)
        cache = RegistryCache()
        cache["requests"] = {"info": {}}
        cache.fetched_at["requests"] = time.time() - 1000
        save_cache(cache)
        loaded = load_cache()
        loaded["httpx"] = {"info": {}}
        save_cache(loaded)
        assert load_cache().fetched_at["requests"] == cache.fetched_at["requests"]

    def test_save_is_atomic(self, monkeypatch, tmp_path: Path) -> None:
        _use_tmp_cache(monkeypatch, tmp_path)
        save_cache({"requests": {"info": {}}})
        assert [p.name for p in cache_path().parent.iterdir()] == ["pypi.json"]

    def test_round_trips_github_entries(self, monkeypatch, tmp_path: Path) -> None:
        _use_tmp_cache(monkeypatch, tmp_path)
        tags = [{"name": "v4", "commit": {"sha": "0" * 40}}]
        save_cache({"github:actions/checkout": tags})
        assert load_cache()["github:actions/checkout"] == tags
