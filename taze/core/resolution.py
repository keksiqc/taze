"""Dependency filtering and registry resolution."""

from __future__ import annotations

import re
from collections.abc import Callable, MutableMapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NamedTuple

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet

from taze.config import package_mode_for
from taze.io.parsers import parse_selectors, selector_ranges
from taze.models import PRE_RELEASE_MODES, DepInfo, calc_bump
from taze.registries.github import fetch_github_action_info
from taze.registries.pypi import PypiResolution, fetch_pypi_info


if TYPE_CHECKING:
    from packaging.version import Version

    from taze.config import TazeConfig


Selectors = list[tuple[re.Pattern[str], tuple[str, ...]]]
Cache = MutableMapping[str, Any]


@dataclass(frozen=True)
class NameFilter:
    """A package-name pattern plus ``name@range`` selectors, as parsed from one CLI option."""

    pattern: re.Pattern[str] | None = None
    selectors: Selectors = field(default_factory=list)

    @classmethod
    def parse(cls, value: str | list[str] | None) -> NameFilter:
        """Build from ``-n``/``-x`` style input. Raises ``re.error`` for a bad ``/regex/``."""
        pattern, selectors = parse_selectors(value)
        return cls(pattern, selectors)

    @property
    def active(self) -> bool:
        return self.pattern is not None or bool(self.selectors)

    def matches_name(self, name: str) -> bool:
        """Whether the bare name matches the pattern (selectors are not consulted)."""
        return self.pattern is not None and self.pattern.match(name) is not None

    def ranges(self, name: str) -> tuple[str, ...]:
        """Version selectors (``requests@2``) that apply to ``name``."""
        return selector_ranges(name, self.selectors)

    def matches(self, name: str) -> bool:
        """Whether the name is covered either by the pattern or by a selector."""
        return self.matches_name(name) or bool(self.ranges(name))


@dataclass(frozen=True)
class ResolvePolicy:
    """Everything that decides *which* version a dependency resolves to.

    Built once per run from :class:`~taze.config.TazeConfig` and shared by
    every lookup, so the resolution functions stay free of CLI plumbing.
    """

    mode: str = "default"
    include_locked: bool = False
    maturity_period: int = 0
    package_modes: dict[str, str] = field(default_factory=dict)
    local_package_names: frozenset[str] = frozenset()
    include: NameFilter = field(default_factory=NameFilter)
    exclude: NameFilter = field(default_factory=NameFilter)
    maturity_exclude: NameFilter = field(default_factory=NameFilter)
    concurrency: int = 10
    force: bool = False
    request_timeout: float = 10.0
    retries: int = 2
    interactive: bool = False
    github_actions_style: str = "auto"

    @classmethod
    def from_config(
        cls,
        cfg: TazeConfig,
        *,
        local_package_names: frozenset[str] = frozenset(),
        github_actions_style: str | None = None,
        retries: int | None = None,
    ) -> ResolvePolicy:
        """Derive a policy from CLI/config settings. Raises ``re.error`` for a bad filter regex."""
        return cls(
            mode=cfg.mode,
            include_locked=cfg.include_locked,
            maturity_period=cfg.maturity_period,
            package_modes=cfg.package_mode,
            local_package_names=local_package_names,
            include=NameFilter.parse(cfg.include),
            exclude=NameFilter.parse(cfg.exclude),
            maturity_exclude=NameFilter.parse(cfg.maturity_period_exclude),
            concurrency=cfg.concurrency,
            force=cfg.force,
            request_timeout=cfg.request_timeout,
            retries=cfg.retries if retries is None else retries,
            interactive=cfg.interactive,
            github_actions_style=github_actions_style or cfg.github_actions_style,
        )

    @property
    def pre(self) -> bool:
        return self.mode in PRE_RELEASE_MODES

    def maturity_period_for(self, name: str) -> int:
        return 0 if self.maturity_exclude.matches_name(name) else self.maturity_period

    def mode_for(self, info: DepInfo) -> str:
        return info.effective_mode or self.mode

    def action_style_for(self, info: DepInfo) -> str:
        return (info.action_style or "tag") if self.github_actions_style == "auto" else self.github_actions_style

    def wants(self, info: DepInfo) -> bool:
        """Apply the include/exclude, local-package, locked-pin, and per-package ``ignore`` rules."""
        if self.include.active and not self.include.matches(info.name):
            return False
        if self.exclude.matches_name(info.name):
            return False
        if info.name in self.local_package_names:
            return False
        if info.is_locked and not self.include_locked:
            return False
        info.effective_mode = package_mode_for(info.name, self.package_modes)
        return info.effective_mode != "ignore"


class Resolution(NamedTuple):
    """What a registry told us about one dependency."""

    latest: str | None
    release_date: str | None
    current_release_date: str | None
    target_sha: str | None = None
    available_versions: tuple[str, ...] = ()

    def apply(self, info: DepInfo) -> None:
        info.latest = self.latest
        info.release_date = self.release_date
        info.current_release_date = self.current_release_date
        info.action_target_sha = self.target_sha
        info.available_versions = self.available_versions
        info.fetch_error = self.latest is None


def resolve_deps(
    infos: Sequence[DepInfo],
    policy: ResolvePolicy,
    *,
    cache: Cache | None = None,
    python_version: Version | None = None,
    on_progress: Callable[[int], None] | None = None,
) -> list[DepInfo]:
    """Filter ``infos`` by the policy, then fetch registry metadata for the rest concurrently."""
    selected = [info for info in infos if policy.wants(info)]
    if not selected:
        return selected

    with ThreadPoolExecutor(max_workers=max(1, policy.concurrency)) as pool:
        futures = {
            pool.submit(fetch_resolution, info, policy, cache=cache, python_version=python_version): info
            for info in selected
        }
        for future in as_completed(futures):
            info = futures[future]
            try:
                future.result().apply(info)
            except (AttributeError, OSError, TypeError, ValueError):
                info.fetch_error = True
            info.bump = calc_bump(info.current, info.latest)
            if on_progress is not None:
                on_progress(1)

    return selected


def fetch_resolution(
    info: DepInfo,
    policy: ResolvePolicy,
    *,
    cache: Cache | None,
    python_version: Version | None = None,
) -> Resolution:
    """Look one dependency up on the registry that owns it."""
    if info.source == "github-actions":
        return _fetch_action(info, policy, cache=cache)
    return _fetch_package(info, policy, cache=cache, python_version=python_version)


def _fetch_action(info: DepInfo, policy: ResolvePolicy, *, cache: Cache | None) -> Resolution:
    result = fetch_github_action_info(
        info.action_repo or info.name,
        current_version=info.current,
        mode=policy.mode_for(info),
        pre=policy.pre,
        maturity_period=policy.maturity_period_for(info.name),
        exclude_ranges=policy.exclude.ranges(info.name),
        include_ranges=policy.include.ranges(info.name),
        maturity_exclude_ranges=policy.maturity_exclude.ranges(info.name),
        timeout=policy.request_timeout,
        retries=policy.retries,
        cache=cache,
        force=policy.force,
        precise=policy.action_style_for(info) == "sha",
    )
    return Resolution(result.latest, result.release_date, result.current_release_date, result.target_sha)


def _fetch_package(
    info: DepInfo,
    policy: ResolvePolicy,
    *,
    cache: Cache | None,
    python_version: Version | None,
) -> Resolution:
    mode = policy.mode_for(info)
    specifier = _resolution_specifier(info, mode=mode, include_locked=policy.include_locked)
    result = _lookup(
        info, policy, mode=mode, specifier=specifier, cache=cache, python=python_version, force=policy.force
    )
    choices: tuple[str, ...] = ()
    if policy.interactive:
        choices = _interactive_versions(info, policy, specifier=specifier, cache=cache, python_version=python_version)
    return Resolution(result.latest, result.release_date, result.current_release_date, None, choices)


def _lookup(
    info: DepInfo,
    policy: ResolvePolicy,
    *,
    mode: str,
    specifier: SpecifierSet | None,
    cache: Cache | None,
    python: Version | None,
    force: bool,
) -> PypiResolution:
    return fetch_pypi_info(
        info.name,
        pre=policy.pre,
        current_version=info.current,
        specifier=specifier,
        mode=mode,
        maturity_period=policy.maturity_period_for(info.name),
        exclude_ranges=policy.exclude.ranges(info.name),
        include_ranges=policy.include.ranges(info.name),
        maturity_exclude_ranges=policy.maturity_exclude.ranges(info.name),
        timeout=policy.request_timeout,
        retries=policy.retries,
        cache=cache,
        force=force,
        python_version=python,
    )


def _resolution_specifier(info: DepInfo, *, mode: str, include_locked: bool) -> SpecifierSet | None:
    """Return the declared PEP 440 range that applies to the selected mode."""
    if mode not in ("default", "stable") or (info.is_locked and include_locked):
        return None
    try:
        return Requirement(info.raw).specifier
    except InvalidRequirement:
        return None


def _interactive_versions(
    info: DepInfo,
    policy: ResolvePolicy,
    *,
    specifier: SpecifierSet | None,
    cache: Cache | None,
    python_version: Version | None,
) -> tuple[str, ...]:
    """Offer the same patch/minor/latest choices as the upstream selector; served from cache."""
    if cache is None:
        return (info.latest,) if info.latest else ()
    choices: list[str] = []
    for choice_mode in ("latest", "minor", "patch"):
        result = _lookup(
            info, policy, mode=choice_mode, specifier=specifier, cache=cache, python=python_version, force=False
        )
        if result.latest and result.latest not in choices and result.latest != info.current:
            choices.append(result.latest)
    return tuple(choices)
