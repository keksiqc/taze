"""Benchmarks for the on-disk registry cache (orjson-backed load/save)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from taze.io.cache import cache_path, load_cache, save_cache


if TYPE_CHECKING:
    from pathlib import Path


PACKAGE_COUNT = 300
RELEASE_COUNT = 60


def _cache() -> dict[str, dict]:
    releases = {
        f"1.{minor}.0": [{"requires_python": ">=3.9", "upload_time": "2023-01-01T00:00:00", "yanked": False}]
        for minor in range(RELEASE_COUNT)
    }
    payload = {"info": {"version": f"1.{RELEASE_COUNT - 1}.0", "requires_python": ">=3.9"}, "releases": releases}
    return {f"pkg-{i}": payload for i in range(PACKAGE_COUNT)}


class TestCacheBenchmarks:
    @pytest.mark.benchmark(group="cache_io")
    def test_save_cache(self, benchmark, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        cache = _cache()
        benchmark(lambda: save_cache(cache))
        assert cache_path().exists()

    @pytest.mark.benchmark(group="cache_io")
    def test_load_cache(self, benchmark, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        save_cache(_cache())
        loaded = benchmark(load_cache)
        assert len(loaded) == PACKAGE_COUNT
