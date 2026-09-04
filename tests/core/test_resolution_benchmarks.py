"""Benchmarks for version resolution: cached vs. non-cached PyPI lookups.

These run as ordinary pytest tests (so `pytest` alone still exercises them
for correctness) but use ``pytest-benchmark``'s `benchmark` fixture to time
each variant and report cached-vs-uncached statistics. Run
``pytest --benchmark-only`` to see the timing comparison, or
``pytest --benchmark-skip`` to skip timing and just check correctness.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from taze.core.resolution import resolve_deps
from taze.registries.pypi import fetch_pypi_info


PACKAGE_COUNT = 100
RELEASE_COUNT = 50


def _releases(count: int) -> dict[str, list[dict]]:
    return {
        f"1.{minor}.0": [{"upload_time": f"2023-{(minor % 12) + 1:02d}-01T00:00:00", "yanked": False}]
        for minor in range(count)
    }


def _payload(release_count: int = RELEASE_COUNT) -> dict:
    releases = _releases(release_count)
    return {"info": {"version": f"1.{release_count - 1}.0"}, "releases": releases}


def _mock_response(data: dict) -> MagicMock:
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=ctx)
    ctx.__exit__ = MagicMock(return_value=False)
    ctx.read = MagicMock(return_value=json.dumps(data).encode())
    return ctx


def _entries() -> list[tuple[str, int | None]]:
    # A declared lower bound forces the range-aware scan of every release
    # instead of the info.version fast path, in both the cached and
    # uncached case, so the benchmark measures the same resolution work.
    return [(f"pkg-{i}>=1.0.0", i) for i in range(PACKAGE_COUNT)]


def _resolve(cache: dict[str, dict] | None) -> list:
    return resolve_deps(
        _entries(),
        include_pat=None,
        exclude_pat=None,
        pre=False,
        mode="default",
        include_locked=False,
        maturity_period=0,
        maturity_exclude_pat=None,
        package_modes={},
        local_package_names=set(),
        concurrency=8,
        cache=cache,
    )


class TestFetchPypiInfoBenchmark:
    """Single-package lookup: a cold network round-trip vs. a warm cache hit."""

    @pytest.mark.benchmark(group="fetch_pypi_info")
    def test_uncached_lookup(self, benchmark) -> None:
        payload = _payload()
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            version, *_ = benchmark(lambda: fetch_pypi_info("requests", current_version="1.0.0"))
        assert version == f"1.{RELEASE_COUNT - 1}.0"

    @pytest.mark.benchmark(group="fetch_pypi_info")
    def test_cached_lookup(self, benchmark) -> None:
        cache = {"requests": _payload()}
        with patch("urllib.request.urlopen", side_effect=AssertionError("cache hit must not touch the network")):
            version, *_ = benchmark(lambda: fetch_pypi_info("requests", current_version="1.0.0", cache=cache))
        assert version == f"1.{RELEASE_COUNT - 1}.0"


class TestResolveDepsBenchmark:
    """Resolving a whole dependency file: fresh cache vs. warm cache."""

    @pytest.mark.benchmark(group="resolve_deps")
    def test_uncached_resolution(self, benchmark) -> None:
        payload = _payload()
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            resolved = benchmark(lambda: _resolve(cache=None))
        assert len(resolved) == PACKAGE_COUNT
        assert all(info.latest == f"1.{RELEASE_COUNT - 1}.0" for info in resolved)

    @pytest.mark.benchmark(group="resolve_deps")
    def test_cached_resolution(self, benchmark) -> None:
        cache = {f"pkg-{i}": _payload() for i in range(PACKAGE_COUNT)}
        with patch("urllib.request.urlopen", side_effect=AssertionError("cache hit must not touch the network")):
            resolved = benchmark(lambda: _resolve(cache=cache))
        assert len(resolved) == PACKAGE_COUNT
        assert all(info.latest == f"1.{RELEASE_COUNT - 1}.0" for info in resolved)


class TestCacheAvoidsNetworkCalls:
    """Non-timing correctness checks: caching must actually skip the network."""

    def test_uncached_hits_network_per_package(self) -> None:
        payload = _payload()
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)) as urlopen:
            _resolve(cache=None)
        assert urlopen.call_count == PACKAGE_COUNT

    def test_cached_never_touches_network(self) -> None:
        cache = {f"pkg-{i}": _payload() for i in range(PACKAGE_COUNT)}
        with patch("urllib.request.urlopen", side_effect=AssertionError("should not be called")) as urlopen:
            _resolve(cache=cache)
        urlopen.assert_not_called()

    def test_partial_cache_only_fetches_missing_packages(self) -> None:
        payload = _payload()
        cache = {f"pkg-{i}": payload for i in range(PACKAGE_COUNT // 2)}
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)) as urlopen:
            _resolve(cache=cache)
        assert urlopen.call_count == PACKAGE_COUNT - PACKAGE_COUNT // 2

    def test_cached_and_uncached_resolve_to_the_same_versions(self) -> None:
        payload = _payload()
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            uncached = _resolve(cache=None)
        cache = {f"pkg-{i}": payload for i in range(PACKAGE_COUNT)}
        with patch("urllib.request.urlopen", side_effect=AssertionError("should not be called")):
            cached = _resolve(cache=cache)
        assert {info.name: info.latest for info in uncached} == {info.name: info.latest for info in cached}
