from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from taze.config import TazeConfig
from taze.core.resolution import NameFilter, Resolution, ResolvePolicy, resolve_deps
from taze.io.parsers import parse_dep_string
from taze.models import DepInfo
from taze.registries.github import ActionResolution
from taze.registries.pypi import PypiResolution


def _infos(*raws: str):
    return [info for raw in raws if (info := parse_dep_string(raw)) is not None]


def _resolve(policy: ResolvePolicy, *raws: str, latest: str | None = "2.0"):
    with patch("taze.core.resolution.fetch_pypi_info", return_value=PypiResolution(latest, None, None)) as fetch:
        resolved = resolve_deps(_infos(*raws), policy)
    return resolved, fetch


class TestPolicyFilters:
    def test_skips_local_workspace_packages(self) -> None:
        resolved, fetch = _resolve(ResolvePolicy(local_package_names=frozenset({"shared-lib"})), "shared-lib>=1.0")
        assert resolved == []
        fetch.assert_not_called()

    def test_include_pattern_limits_lookups(self) -> None:
        policy = ResolvePolicy(include=NameFilter.parse("httpx"))
        resolved, _ = _resolve(policy, "requests>=1", "httpx>=1")
        assert [info.name for info in resolved] == ["httpx"]

    def test_include_selector_alone_limits_lookups(self) -> None:
        policy = ResolvePolicy(include=NameFilter.parse("httpx@1"))
        resolved, _ = _resolve(policy, "requests>=1", "httpx>=1")
        assert [info.name for info in resolved] == ["httpx"]

    def test_exclude_pattern_drops_names_but_exclude_selector_does_not(self) -> None:
        policy = ResolvePolicy(exclude=NameFilter.parse("/^pytest/,requests@2"))
        resolved, _ = _resolve(policy, "pytest-cov>=1", "requests>=1")
        assert [info.name for info in resolved] == ["requests"]

    def test_locked_pins_are_skipped_unless_requested(self) -> None:
        resolved, _ = _resolve(ResolvePolicy(), "requests==1.0")
        assert resolved == []
        resolved, _ = _resolve(ResolvePolicy(include_locked=True), "requests==1.0")
        assert [info.name for info in resolved] == ["requests"]

    def test_package_mode_ignore_and_override(self) -> None:
        policy = ResolvePolicy(package_modes={"setuptools": "ignore", "django": "minor"})
        resolved, _ = _resolve(policy, "setuptools>=60", "django>=4")
        assert [(info.name, info.effective_mode) for info in resolved] == [("django", "minor")]

    def test_maturity_period_is_lifted_for_excluded_names(self) -> None:
        policy = ResolvePolicy(maturity_period=7, maturity_exclude=NameFilter.parse("internal-*"))
        assert policy.maturity_period_for("internal-tools") == 0
        assert policy.maturity_period_for("requests") == 7


class TestResolveOutcome:
    def test_marks_bump_and_dates(self) -> None:
        with patch(
            "taze.core.resolution.fetch_pypi_info", return_value=PypiResolution("2.0", "2026-01-01", "2025-01-01")
        ):
            (info,) = resolve_deps(_infos("requests>=1.0"), ResolvePolicy())
        assert (info.latest, info.bump, info.release_date, info.current_release_date) == (
            "2.0",
            "major",
            "2026-01-01",
            "2025-01-01",
        )
        assert info.fetch_error is False

    def test_failed_lookup_is_flagged_not_raised(self) -> None:
        resolved, _ = _resolve(ResolvePolicy(), "requests>=1.0", latest=None)
        assert resolved[0].fetch_error is True
        assert resolved[0].bump == "?"

    def test_exception_in_worker_is_flagged(self) -> None:
        with patch("taze.core.resolution.fetch_pypi_info", side_effect=ValueError("boom")):
            (info,) = resolve_deps(_infos("requests>=1.0"), ResolvePolicy())
        assert info.fetch_error is True

    def test_progress_callback_fires_per_package(self) -> None:
        ticks: list[int] = []
        with patch("taze.core.resolution.fetch_pypi_info", return_value=PypiResolution("2.0", None, None)):
            resolve_deps(_infos("a>=1", "b>=1", "c>=1"), ResolvePolicy(), on_progress=ticks.append)
        assert sum(ticks) == 3

    def test_action_lookup_uses_github(self) -> None:
        dep = DepInfo(
            raw="actions/checkout@v4",
            name="actions/checkout",
            current="v4",
            operator=None,
            source="github-actions",
            action_repo="actions/checkout",
            action_style="tag",
        )
        with patch(
            "taze.core.resolution.fetch_github_action_info",
            return_value=ActionResolution("v5", None, None, "a" * 40),
        ) as fetch:
            (resolved,) = resolve_deps([dep], ResolvePolicy(github_actions_style="sha"))
        assert (resolved.latest, resolved.action_target_sha, resolved.bump) == ("v5", "a" * 40, "major")
        assert fetch.call_args.kwargs["precise"] is True


class TestPolicyFromConfig:
    def test_derives_from_config(self) -> None:
        cfg = TazeConfig(mode="newest", include="httpx", retries=5, github_actions_style="tag")
        policy = ResolvePolicy.from_config(cfg, retries=0, github_actions_style="sha")
        assert (policy.mode, policy.pre, policy.retries, policy.github_actions_style) == ("newest", True, 0, "sha")
        assert policy.include.matches("httpx")

    def test_invalid_regex_raises(self) -> None:
        with pytest.raises(re.error):
            ResolvePolicy.from_config(TazeConfig(include="/[/"))


def test_resolution_apply_sets_fetch_error_when_missing() -> None:
    info = DepInfo(raw="x", name="x", current="1", operator=">=")
    Resolution(None, None, None).apply(info)
    assert info.fetch_error is True
