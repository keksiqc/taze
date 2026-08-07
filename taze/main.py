from __future__ import annotations

import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, cast

import typer
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.prompt import Confirm

from taze import __version__
from taze.actions import fetch_github_action_info, is_action_file, parse_actions, write_action_updates
from taze.cache import load_cache, save_cache
from taze.config import ConfigError, TazeConfig, load_config, package_mode_for, resolve_config
from taze.discovery import discover_files
from taze.display import console, interactive_select, render_file_header, render_group, render_json
from taze.installers import install_command
from taze.models import MODES, PRE_RELEASE_MODES, DepInfo, calc_bump
from taze.parsers import (
    parse_dep_string,
    parse_project_name,
    parse_pyproject_entries,
    parse_selectors,
    selector_ranges,
)
from taze.pypi import fetch_pypi_info
from taze.writers import write_pyproject_updates, write_requirements_updates


if TYPE_CHECKING:
    from collections.abc import Callable


Entry = tuple[str, int | None] | tuple[str, int | None, dict[str, str]] | DepInfo


app = typer.Typer(
    name="taze",
    help="🥬 Keep your Python deps fresh",
    add_completion=False,
    rich_markup_mode="rich",
    no_args_is_help=False,
)

SORT_CHOICES = ("name-asc", "name-desc", "diff-asc", "diff-desc")


# ─── Resolution ───────────────────────────────────────────────────────────────


def resolve_deps(
    entries: list[tuple[str, int | None] | tuple[str, int | None, dict[str, str]] | DepInfo],
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
                i,
                mode=i.effective_mode or mode,
                pre=pre,
                include_locked=include_locked,
                maturity_period=maturity_period,
                maturity_exclude_pat=maturity_exclude_pat,
                maturity_exclude_ranges=selector_ranges(i.name, maturity_exclude_selectors),
                exclude_ranges=selector_ranges(i.name, exclude_selectors),
                include_ranges=selector_ranges(i.name, include_selectors),
                cache=cache,
                action_cache=action_cache,
                force=force,
                request_timeout=request_timeout,
                retries=retries,
                interactive=interactive,
                github_actions_style=github_actions_style,
            ): i
            for i in infos
        }
        for fut in as_completed(futures):
            info = futures[fut]
            try:
                version, latest_date, current_date, target_sha, available_versions = fut.result()
                info.latest = version
                info.available_versions = available_versions
                info.release_date = latest_date
                info.current_release_date = current_date
                info.action_target_sha = target_sha
                info.fetch_error = version is None
            except AttributeError, OSError, TypeError, ValueError:
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


# ─── File discovery ───────────────────────────────────────────────────────────


# ─── CLI ──────────────────────────────────────────────────────────────────────


@app.command()
def main(
    context: typer.Context,
    mode: Annotated[
        str,
        typer.Argument(
            help=("Update mode: [dim]default | major | minor | patch | latest | newest | stable | next[/]"),
            show_default=False,
        ),
    ] = "default",
    cwd: Annotated[
        Path | None,
        typer.Option("--cwd", "-C", help="Working directory", show_default=False),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to a taze.toml configuration file"),
    ] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Bypass the local metadata cache")] = False,
    write: Annotated[bool, typer.Option("--write", "-w", help="Write updates back to file")] = False,
    install: Annotated[
        bool,
        typer.Option(
            "--install",
            "-i",
            help="Install directly after bumping (implies [cyan]-w[/])",
        ),
    ] = False,
    update: Annotated[
        bool,
        typer.Option("--update", "-u", help="Alias for [cyan]--install[/]"),
    ] = False,
    recursive: Annotated[
        bool,
        typer.Option(
            "--recursive",
            "-r",
            help="Recursively search for pyproject.toml / requirements*.txt",
        ),
    ] = False,
    ignore_paths: Annotated[
        str | None,
        typer.Option("--ignore-paths", help="Comma-separated glob paths to skip during recursive scans"),
    ] = None,
    ignore_other_workspaces: Annotated[
        bool,
        typer.Option(
            "--ignore-other-workspaces/--include-other-workspaces",
            help="Skip nested repositories and workspaces when scanning recursively",
        ),
    ] = True,
    interactive: Annotated[
        bool,
        typer.Option(
            "--interactive",
            "-I",
            help="Interactive mode — choose which packages to update",
        ),
    ] = False,
    include: Annotated[
        str | None,
        typer.Option(
            "--include",
            "-n",
            help="Only check these deps (comma-separated names or [dim]/regex/[/])",
        ),
    ] = None,
    exclude: Annotated[
        str | None,
        typer.Option(
            "--exclude",
            "-x",
            help="Skip these deps (comma-separated names or [dim]/regex/[/])",
        ),
    ] = None,
    all_deps: Annotated[bool, typer.Option("--all", "-a", help="Show up-to-date packages too")] = False,
    group: Annotated[
        bool,
        typer.Option("--group/--no-group", help="Group dependencies by source file on display"),
    ] = True,
    include_locked: Annotated[
        bool,
        typer.Option("--include-locked", "-l", help="Include exact (==) version pins"),
    ] = False,
    maturity_period: Annotated[
        int,
        typer.Option("--maturity-period", help="Wait this many days before accepting a new release"),
    ] = 0,
    maturity_period_exclude: Annotated[
        str | None,
        typer.Option("--maturity-period-exclude", help="Packages exempt from the maturity policy"),
    ] = None,
    sort: Annotated[
        str | None,
        typer.Option("--sort", help="Sort by: name-asc | name-desc | diff-asc | diff-desc"),
    ] = "diff-asc",
    fail_on_outdated: Annotated[
        bool,
        typer.Option(
            "--fail-on-outdated",
            "--check",
            help="Exit with code 1 if outdated dependencies are found",
        ),
    ] = False,
    silent: Annotated[bool, typer.Option("--silent", "-s", help="No output")] = False,
    output_json: Annotated[bool, typer.Option("--json", help="Machine-readable JSON output")] = False,
    github_actions: Annotated[
        bool,
        typer.Option(
            "--github-actions/--no-github-actions",
            help="Check versioned GitHub Actions references",
        ),
    ] = True,
    github_actions_style: Annotated[
        str,
        typer.Option("--github-actions-style", help="Action write style: auto | tag | sha"),
    ] = "auto",
    github_actions_pin: Annotated[
        bool,
        typer.Option(
            "--github-actions-pin",
            help="Pin GitHub Actions to their commit SHA even if already up to date (implies sha style)",
        ),
    ] = False,
    version: Annotated[
        bool,
        typer.Option("--version", "-v", help="Show version and exit", is_eager=True),
    ] = False,
    concurrency: Annotated[
        int,
        typer.Option("--concurrency", help="Number of concurrent registry requests"),
    ] = 10,
    request_timeout: Annotated[
        float,
        typer.Option("--request-timeout", help="Registry request timeout in seconds"),
    ] = 10.0,
    retries: Annotated[
        int,
        typer.Option("--retry", help="Retries after a failed registry request"),
    ] = 2,
    no_retry: Annotated[bool, typer.Option("--no-retry", help="Disable registry retries")] = False,
) -> None:
    if version:
        console.print(f"taze/{__version__}")
        raise typer.Exit(0)

    # ── Load and merge configuration ────────────────────────────────────────
    # Precedence: explicit CLI flag > environment > taze.toml > pyproject.toml > CLI default.
    root = (cwd or Path()).resolve()
    config_path = (root / config).resolve() if config and not config.is_absolute() else config
    try:
        project_config = load_config(root, config_path)
    except ConfigError as error:
        console.print(f"[red]✗[/]  Invalid configuration: {error}")
        raise typer.Exit(1) from error

    cli_config = TazeConfig(
        mode=mode,
        include=include,
        exclude=exclude,
        recursive=recursive,
        ignore_paths=ignore_paths,
        ignore_other_workspaces=ignore_other_workspaces,
        interactive=interactive,
        all_deps=all_deps,
        group=group,
        include_locked=include_locked,
        maturity_period=maturity_period,
        maturity_period_exclude=maturity_period_exclude,
        sort=sort,
        fail_on_outdated=fail_on_outdated,
        silent=silent,
        output_json=output_json,
        github_actions=github_actions,
        github_actions_style=github_actions_style,
        github_actions_pin=github_actions_pin,
        concurrency=concurrency,
        request_timeout=request_timeout,
        retries=retries,
        write=write,
        install=install,
        update=update,
        force=force,
    )
    cfg = resolve_config(context, cli_config, project_config)

    # These three get mutated further below (install/-w prompts, pin-actions
    # implying sha style), so they stay as plain locals; everything else is
    # read straight off `cfg` from here on.
    write = cfg.write
    install = cfg.install
    github_actions_style = cfg.github_actions_style

    # ── Cross-field rules and validation ────────────────────────────────────
    if cfg.mode not in MODES:
        console.print(f"[red]✗[/]  Unknown mode [bold]{cfg.mode!r}[/]. Available: {' | '.join(MODES)}")
        raise typer.Exit(1)
    if cfg.sort and cfg.sort not in SORT_CHOICES:
        console.print(f"[red]✗[/]  --sort must be one of: {', '.join(SORT_CHOICES)}")
        raise typer.Exit(1)
    if github_actions_style not in ("auto", "tag", "sha"):
        console.print("[red]✗[/]  --github-actions-style must be auto, tag, or sha")
        raise typer.Exit(1)
    if cfg.concurrency < 1 or cfg.request_timeout <= 0 or cfg.retries < 0 or cfg.maturity_period < 0:
        console.print(
            "[red]✗[/]  concurrency, timeout, and maturity-period must be positive; retries cannot be negative"
        )
        raise typer.Exit(1)

    if cfg.github_actions_pin and github_actions_style == "auto":
        github_actions_style = "sha"
    if install or cfg.update or cfg.github_actions_pin:
        write = True

    pre = cfg.mode in PRE_RELEASE_MODES

    try:
        include_pat, include_selectors = parse_selectors(cfg.include)
        exclude_pat, exclude_selectors = parse_selectors(cfg.exclude)
        maturity_exclude_pat, maturity_exclude_selectors = parse_selectors(cfg.maturity_period_exclude)
    except re.error as error:
        console.print(f"[red]✗[/]  Invalid dependency filter: {error}")
        raise typer.Exit(1) from error

    # ── Collect files ─────────────────────────────────────────────────────────
    ignored = _path_patterns(cfg.ignore_paths)
    target_files = discover_files(
        root,
        recursive=cfg.recursive,
        ignore_paths=ignored,
        ignore_other_workspaces=cfg.ignore_other_workspaces,
        github_actions=cfg.github_actions,
    )

    if not target_files:
        if not cfg.silent:
            console.print(f"[red]✗[/]  No supported dependency files found in {root}")
        raise typer.Exit(1)

    local_package_names: set[str] = set()
    for file_path in target_files:
        if file_path.name != "pyproject.toml":
            continue
        try:
            name = parse_project_name(file_path)
        except AttributeError, OSError, TypeError, ValueError:
            name = None
        if name:
            local_package_names.add(name)

    # ── Build entries per file ────────────────────────────────────────────────
    # file_path → group_label → raw entries
    raw_file_groups: dict[Path, dict[str, list[Entry]]] = {}

    for file_path in target_files:
        if file_path.name == "pyproject.toml":
            try:
                raw_groups = parse_pyproject_entries(file_path)
            except (AttributeError, OSError, TypeError, ValueError) as e:
                if not cfg.silent:
                    console.print(f"[red]✗[/]  Failed to parse {file_path}: {e}")
                continue
            raw_file_groups[file_path] = {
                label: [(raw, None, metadata) for raw, metadata in entries] for label, entries in raw_groups.items()
            }
        elif is_action_file(file_path):
            action_infos = parse_actions(file_path)
            if action_infos:
                raw_file_groups[file_path] = {"github-actions": cast(list[Entry], action_infos)}
        else:
            try:
                lines = file_path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError) as e:
                if not cfg.silent:
                    console.print(f"[red]✗[/]  Failed to parse {file_path}: {e}")
                continue
            raw_file_groups[file_path] = {
                "requirements": [
                    (line, lineno)
                    for lineno, line in enumerate(lines, 1)
                    if line.strip() and not line.lstrip().startswith(("#", "-"))
                ],
            }

    if not raw_file_groups:
        raise typer.Exit(1)

    total_packages = sum(len(entries) for groups in raw_file_groups.values() for entries in groups.values())

    # ── Resolve registry metadata ────────────────────────────────────────────
    registry_cache = load_cache(force=cfg.force)
    action_cache: dict[str, list[dict]] = {}
    resolved: dict[Path, dict[str, list[DepInfo]]] = {}

    with Progress(
        TextColumn("[dim]Checking packages on registries…[/]"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
        disable=cfg.silent or cfg.output_json,
    ) as progress:
        task_id = progress.add_task("checking", total=total_packages)

        def on_progress(n: int) -> None:
            progress.update(task_id, advance=n)

        for file_path, groups in raw_file_groups.items():
            resolved[file_path] = {}
            for label, entries in groups.items():
                resolved[file_path][label] = resolve_deps(
                    entries,
                    include_pat=include_pat,
                    exclude_pat=exclude_pat,
                    pre=pre,
                    mode=cfg.mode,
                    include_locked=cfg.include_locked,
                    maturity_period=cfg.maturity_period,
                    maturity_exclude_pat=maturity_exclude_pat,
                    package_modes=cfg.package_mode,
                    local_package_names=local_package_names,
                    concurrency=cfg.concurrency,
                    on_progress=on_progress,
                    include_selectors=include_selectors,
                    exclude_selectors=exclude_selectors,
                    maturity_exclude_selectors=maturity_exclude_selectors,
                    cache=registry_cache,
                    action_cache=action_cache,
                    force=cfg.force,
                    request_timeout=cfg.request_timeout,
                    retries=0 if no_retry else cfg.retries,
                    interactive=cfg.interactive,
                    github_actions_style=github_actions_style,
                )

    save_cache(registry_cache)

    # ── JSON output ───────────────────────────────────────────────────────────
    if cfg.output_json:
        render_json(
            {str(fp): grps for fp, grps in resolved.items()},
            mode=cfg.mode,
            show_up_to_date=cfg.all_deps,
        )
        if write:
            for file_path, groups in resolved.items():
                if is_action_file(file_path):
                    write_action_updates(
                        file_path,
                        [info for infos in groups.values() for info in infos],
                        mode=cfg.mode,
                        style=github_actions_style,
                        pin_unchanged=cfg.github_actions_pin,
                    )
                elif file_path.name == "pyproject.toml":
                    write_pyproject_updates(file_path, groups, mode=cfg.mode)
                else:
                    write_requirements_updates(
                        file_path,
                        [info for infos in groups.values() for info in infos],
                        mode=cfg.mode,
                    )
        total_outdated = _count_outdated(resolved, cfg.mode)
        raise typer.Exit(1 if (cfg.fail_on_outdated and total_outdated) else 0)

    # ── Interactive selection ─────────────────────────────────────────────────
    total_outdated = _count_outdated(resolved, cfg.mode)
    selected_for_update: set[int] | None = None  # None = all

    # --github-actions-pin can still have work to do (converting a tag pin to a SHA
    # pin) even when nothing is version-outdated, so it must bypass the
    # "nothing to update" early exit below.
    has_pinnable_actions = cfg.github_actions_pin and any(
        i.source == "github-actions"
        and i.action_target_sha
        and i.action_style != "sha"
        and (i.action_style if github_actions_style == "auto" else github_actions_style) == "sha"
        for groups in resolved.values()
        for infos in groups.values()
        for i in infos
    )

    # File labels double as headers, so disambiguate same-named files (e.g. a
    # recursive scan with several pyproject.toml) with their relative path.
    name_counts: dict[str, int] = {}
    for file_path in resolved:
        name_counts[file_path.name] = name_counts.get(file_path.name, 0) + 1
    file_labels: dict[Path, str] = {}
    for file_path in resolved:
        if name_counts[file_path.name] == 1:
            file_labels[file_path] = file_path.name
        else:
            try:
                file_labels[file_path] = str(file_path.relative_to(root))
            except ValueError:
                file_labels[file_path] = str(file_path)

    if cfg.interactive and not cfg.silent:
        interactive_categories: list[tuple[str, list[tuple[str, list[DepInfo]]]]] = []
        all_outdated: list[DepInfo] = []
        for file_path, groups in resolved.items():
            category_groups = (
                groups.items() if cfg.group else [("dependencies", [i for infos in groups.values() for i in infos])]
            )
            file_groups: list[tuple[str, list[DepInfo]]] = []
            for label, infos in category_groups:
                candidates = [i for i in infos if i.is_shown(cfg.mode) and not i.fetch_error]
                if candidates:
                    file_groups.append((label, candidates))
                    all_outdated.extend(candidates)
            if file_groups:
                interactive_categories.append((file_labels[file_path], file_groups))
        chosen = interactive_select(all_outdated, interactive_categories)
        selected_for_update = {id(info) for info in chosen}
        total_outdated = len(chosen)
        console.print()
        if total_outdated == 0:
            # A cancelled or fully-deselected picker isn't "up to date" — stay quiet.
            raise typer.Exit(0)

    if total_outdated == 0 and not has_pinnable_actions:
        if not cfg.silent:
            console.print("[green]dependencies are already up-to-date[/]")
        raise typer.Exit(0)

    # ── Rich display ──────────────────────────────────────────────────────────
    if not cfg.silent:
        console.print()

    for file_path, groups in resolved.items():
        display_groups = (
            groups
            if selected_for_update is None
            else {label: [i for i in infos if id(i) in selected_for_update] for label, infos in groups.items()}
        )

        if not cfg.silent:
            all_infos = [i for infos in display_groups.values() for i in infos]
            if not cfg.all_deps and not any(i.is_shown(cfg.mode) or i.fetch_error for i in all_infos):
                continue

            # Compute column widths across all groups in this file so every
            # group aligns to the same grid.
            from taze.display import _age

            col_widths = (
                max((len(i.name) for i in all_infos), default=0),
                max((len(i.current_spec) for i in all_infos), default=0),
                max((len(_age(i.current_release_date)[0]) for i in all_infos), default=0),
                max((len(_age(i.release_date)[0]) for i in all_infos), default=0),
                max((len(i.latest_spec) for i in all_infos), default=0),
            )

            console.print(render_file_header(file_labels[file_path], all_infos, cfg.mode))
            console.print()

            display_groups = (
                display_groups
                if cfg.group
                else {"dependencies": [i for infos in display_groups.values() for i in infos]}
            )
            for label, infos in display_groups.items():
                if render_group(
                    label,
                    infos,
                    mode=cfg.mode,
                    show_up_to_date=cfg.all_deps,
                    sort=cfg.sort,
                    col_widths=col_widths,
                ):
                    console.print()

    if cfg.interactive and not cfg.silent and not write:
        write = Confirm.ask("  [green]Write updates?[/]", default=True, console=console)
        console.print()

    # ── Write ─────────────────────────────────────────────────────────────────
    if write:
        total_written = 0
        for file_path, groups in resolved.items():
            # Filter to selected packages if in interactive mode
            if selected_for_update is not None:
                filtered: dict[str, list[DepInfo]] = {
                    label: [i for i in infos if id(i) in selected_for_update] for label, infos in groups.items()
                }
            else:
                filtered = groups

            if is_action_file(file_path):
                flat = [i for infos in filtered.values() for i in infos]
                updated = write_action_updates(
                    file_path,
                    flat,
                    mode=cfg.mode,
                    style=github_actions_style,
                    pin_unchanged=cfg.github_actions_pin,
                )
            elif file_path.name == "pyproject.toml":
                updated = write_pyproject_updates(file_path, filtered, mode=cfg.mode)
            else:
                flat = [i for infos in filtered.values() for i in infos]
                updated = write_requirements_updates(file_path, flat, mode=cfg.mode)

            total_written += updated
            if updated and not cfg.silent:
                console.print(f"  [green]✓[/]  Wrote [bold]{updated}[/] update(s) to [cyan]{file_path.name}[/]")

        if total_written and not cfg.silent:
            console.print()
            if not install and not cfg.update and not cfg.interactive:
                command_text = " ".join(
                    install_command(next((fp.parent for fp in resolved if fp.name == "pyproject.toml"), root))
                )
                console.print(f"  [dim]Run [cyan]{command_text}[/] to install the updates.[/]")
        if total_written and cfg.interactive and not cfg.silent and not install and not cfg.update:
            install = Confirm.ask("  [green]Install now?[/]", default=True, console=console)
            console.print()
    elif not cfg.silent:
        console.print(f"  [dim]Run [cyan]taze -w[/] to write {total_outdated} update(s)[/]")
        console.print()

    # ── Lockfile-aware install ───────────────────────────────────────────────
    if install or cfg.update:
        install_cwd = next(
            (fp.parent for fp in resolved if fp.name == "pyproject.toml"),
            root,
        )
        command = install_command(install_cwd)
        command_text = " ".join(command)
        if not cfg.silent:
            console.print(f"  [dim]Running [cyan]{command_text}[/]…[/]")
        result = subprocess.run(
            command,
            cwd=install_cwd,
            capture_output=cfg.silent,
            check=False,
        )
        if result.returncode != 0:
            if not cfg.silent:
                console.print(f"[red]✗[/]  [bold]{command_text}[/] failed")
            raise typer.Exit(result.returncode)
        if not cfg.silent:
            console.print(f"  [green]✓[/]  [bold]{command_text}[/] complete")
            console.print()

    raise typer.Exit(1 if (cfg.fail_on_outdated and total_outdated) else 0)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _count_outdated(resolved: dict[Path, dict[str, list[DepInfo]]], mode: str) -> int:
    return sum(1 for groups in resolved.values() for infos in groups.values() for i in infos if i.is_shown(mode))


def _path_patterns(value: object) -> tuple[str, ...]:
    """Normalise a comma-separated string or TOML list of glob patterns."""
    if isinstance(value, str):
        return tuple(p.strip() for p in value.split(",") if p.strip())
    if isinstance(value, list):
        return tuple(p for p in value if isinstance(p, str))
    return ()


# ─── Entry point ──────────────────────────────────────────────────────────────


if __name__ == "__main__":
    app()
