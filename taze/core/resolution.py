"""Dependency filtering and registry resolution."""

from __future__ import annotations

import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet

from taze.config import package_mode_for
from taze.io.parsers import parse_dep_string, selector_ranges
from taze.models import DepInfo, calc_bump
from taze.registries.github import fetch_github_action_info
from taze.registries.pypi import fetch_pypi_info


Entry = tuple[str, int | None] | tuple[str, int | None, dict[str, str]] | DepInfo


def resolve_deps(
    entries: list[Entry],
    *,
    include_pat: re.Pattern[str] | None,
    exclude_pat: re.Pattern[str] | None,
    pre: bool,
    mode: str,
    include_locked: bool,
    maturity_period: int,
    maturity_exclude_pat: re.Pattern[str] | None,
    package_modes: object,
    local_package_names: set[str],
    concurrency: int,
    on_progress: Callable[[int], None] | None = None,
    include_selectors: list[tuple[re.Pattern[str], tuple[str, ...]]] | None = None,
    exclude_selectors: list[tuple[re.Pattern[str], tuple[str, ...]]] | None = None,
    maturity_exclude_selectors: list[tuple[re.Pattern[str], tuple[str, ...]]] | None = None,
    cache: dict[str, dict] | None = None,
    action_cache: dict[str, list[dict]] | None = None,
    force: bool = False,
    request_timeout: float = 10.0,
    retries: int = 2,
    interactive: bool = False,
    github_actions_style: str = "auto",
) -> list[DepInfo]:
    """Fetch registry metadata concurrently and return enriched dependencies."""
    include_selectors = include_selectors or []
    exclude_selectors = exclude_selectors or []
    maturity_exclude_selectors = maturity_exclude_selectors or []
    infos: list[DepInfo] = []
    for entry in entries:
        if isinstance(entry, DepInfo):
            info = entry
        else:
            raw, lineno = entry[:2]
            metadata = entry[2] if len(entry) == 3 else {}
            info = parse_dep_string(raw, line_number=lineno, **metadata)
        if info is None:
            continue
        if include_pat and not include_pat.match(info.name) and not selector_ranges(info.name, include_selectors):
            continue
        if not include_pat and include_selectors and not selector_ranges(info.name, include_selectors):
            continue
        if exclude_pat and exclude_pat.match(info.name):
            continue
        if info.name in local_package_names:
            continue
        if info.is_locked and not include_locked:
            continue
        info.effective_mode = package_mode_for(info.name, package_modes)
        if info.effective_mode == "ignore":
            continue
        infos.append(info)

    if not infos:
        return infos

    workers = max(1, concurrency)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _fetch_info,
                info,
                mode=info.effective_mode or mode,
                pre=pre,
                include_locked=include_locked,
                maturity_period=maturity_period,
                maturity_exclude_pat=maturity_exclude_pat,
                maturity_exclude_ranges=selector_ranges(info.name, maturity_exclude_selectors),
                exclude_ranges=selector_ranges(info.name, exclude_selectors),
                include_ranges=selector_ranges(info.name, include_selectors),
                cache=cache,
                action_cache=action_cache,
                force=force,
                request_timeout=request_timeout,
                retries=retries,
                interactive=interactive,
                github_actions_style=github_actions_style,
            ): info
            for info in infos
        }
        for future in as_completed(futures):
            info = futures[future]
            try:
                version, latest_date, current_date, target_sha, available_versions = future.result()
                info.latest = version
                info.available_versions = available_versions
                info.release_date = latest_date
                info.current_release_date = current_date
                info.action_target_sha = target_sha
                info.fetch_error = version is None
            except (AttributeError, OSError, TypeError, ValueError):
                info.fetch_error = True
            info.bump = calc_bump(info.current, info.latest)
            if on_progress is not None:
                on_progress(1)

    return infos


def _fetch_info(
    info: DepInfo,
    *,
    mode: str,
    pre: bool,
    include_locked: bool,
    maturity_period: int,
    maturity_exclude_pat: re.Pattern[str] | None,
    maturity_exclude_ranges: tuple[str, ...],
    exclude_ranges: tuple[str, ...],
    include_ranges: tuple[str, ...],
    cache: dict[str, dict] | None,
    action_cache: dict[str, list[dict]] | None,
    force: bool,
    request_timeout: float,
    retries: int,
    interactive: bool,
    github_actions_style: str = "auto",
) -> tuple[str | None, str | None, str | None, str | None, tuple[str, ...]]:
    if info.source == "github-actions":
        effective_style = info.action_style if github_actions_style == "auto" else github_actions_style
        version, latest_date, current_date, target_sha = fetch_github_action_info(
            info.action_repo or info.name,
            current_version=info.current,
            mode=mode,
            pre=pre,
            maturity_period=0 if maturity_exclude_pat and maturity_exclude_pat.match(info.name) else maturity_period,
            exclude_ranges=exclude_ranges,
            include_ranges=include_ranges,
            maturity_exclude_ranges=maturity_exclude_ranges,
            timeout=request_timeout,
            retries=retries,
            cache=action_cache,
            force=force,
            precise=effective_style == "sha",
        )
        return version, latest_date, current_date, target_sha, ()
    version, latest_date, current_date = fetch_pypi_info(
        info.name,
        pre=pre,
        current_version=info.current,
        specifier=_resolution_specifier(info, mode=mode, include_locked=include_locked),
        mode=mode,
        maturity_period=0 if maturity_exclude_pat and maturity_exclude_pat.match(info.name) else maturity_period,
        exclude_ranges=exclude_ranges,
        include_ranges=include_ranges,
        maturity_exclude_ranges=maturity_exclude_ranges,
        timeout=request_timeout,
        retries=retries,
        cache=cache,
        force=force,
    )
    return (
        version,
        latest_date,
        current_date,
        None,
        _interactive_versions(
            info,
            mode=mode,
            pre=pre,
            include_locked=include_locked,
            maturity_period=maturity_period,
            maturity_exclude_pat=maturity_exclude_pat,
            maturity_exclude_ranges=maturity_exclude_ranges,
            exclude_ranges=exclude_ranges,
            include_ranges=include_ranges,
            cache=cache,
            request_timeout=request_timeout,
            retries=retries,
        )
        if interactive
        else (),
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
    *,
    mode: str,
    pre: bool,
    include_locked: bool,
    maturity_period: int,
    maturity_exclude_pat: re.Pattern[str] | None,
    maturity_exclude_ranges: tuple[str, ...],
    exclude_ranges: tuple[str, ...],
    include_ranges: tuple[str, ...],
    cache: dict[str, dict] | None,
    request_timeout: float,
    retries: int,
) -> tuple[str, ...]:
    """Get the same patch/minor/latest choices exposed by the JS selector."""
    if cache is None:
        return (info.latest,) if info.latest else ()
    specifier = _resolution_specifier(info, mode=mode, include_locked=include_locked)
    period = 0 if maturity_exclude_pat and maturity_exclude_pat.match(info.name) else maturity_period
    choices: list[str] = []
    for choice_mode in ("latest", "minor", "patch"):
        version, _, _ = fetch_pypi_info(
            info.name,
            pre=pre,
            current_version=info.current,
            specifier=specifier,
            mode=choice_mode,
            maturity_period=period,
            exclude_ranges=exclude_ranges,
            include_ranges=include_ranges,
            maturity_exclude_ranges=maturity_exclude_ranges,
            timeout=request_timeout,
            retries=retries,
            cache=cache,
            force=False,
        )
        if version and version not in choices and version != info.current:
            choices.append(version)
    return tuple(choices)
