"""End-to-end dependency check workflow."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import cast

import typer
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.prompt import Confirm

from taze.config import TazeConfig
from taze.core.resolution import Entry, resolve_deps
from taze.io.actions import is_action_file, parse_actions, write_action_updates
from taze.io.cache import load_cache, save_cache
from taze.io.discovery import discover_files
from taze.io.installers import install_command
from taze.io.parsers import parse_project_name, parse_pyproject_entries, parse_selectors
from taze.io.writers import write_pyproject_updates, write_requirements_updates
from taze.models import MODES, PRE_RELEASE_MODES, DepInfo
from taze.ui.display import console, interactive_select, render_file_header, render_group, render_json


SORT_CHOICES = ("name-asc", "name-desc", "diff-asc", "diff-desc")


def run(root: Path, cfg: TazeConfig, *, no_retry: bool = False) -> None:
    """Run one dependency check from discovery through optional installation."""
    write = cfg.write
    install = cfg.install
    github_actions_style = cfg.github_actions_style

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
        except (AttributeError, OSError, TypeError, ValueError):
            name = None
        if name:
            local_package_names.add(name)

    raw_file_groups: dict[Path, dict[str, list[Entry]]] = {}
    for file_path in target_files:
        if file_path.name == "pyproject.toml":
            try:
                raw_groups = parse_pyproject_entries(file_path)
            except (AttributeError, OSError, TypeError, ValueError) as error:
                if not cfg.silent:
                    console.print(f"[red]✗[/]  Failed to parse {file_path}: {error}")
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
            except (OSError, UnicodeError) as error:
                if not cfg.silent:
                    console.print(f"[red]✗[/]  Failed to parse {file_path}: {error}")
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

    if cfg.output_json:
        render_json(
            {str(file_path): groups for file_path, groups in resolved.items()},
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

    total_outdated = _count_outdated(resolved, cfg.mode)
    selected_for_update: set[int] | None = None
    has_pinnable_actions = cfg.github_actions_pin and any(
        info.source == "github-actions"
        and info.action_target_sha
        and info.action_style != "sha"
        and (info.action_style if github_actions_style == "auto" else github_actions_style) == "sha"
        for groups in resolved.values()
        for infos in groups.values()
        for info in infos
    )

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
                groups.items()
                if cfg.group
                else [("dependencies", [info for infos in groups.values() for info in infos])]
            )
            file_groups: list[tuple[str, list[DepInfo]]] = []
            for label, infos in category_groups:
                candidates = [info for info in infos if info.is_shown(cfg.mode) and not info.fetch_error]
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
            raise typer.Exit(0)

    if total_outdated == 0 and not has_pinnable_actions:
        if not cfg.silent:
            console.print("[green]dependencies are already up-to-date[/]")
        raise typer.Exit(0)

    if not cfg.silent:
        console.print()

    for file_path, groups in resolved.items():
        display_groups = (
            groups
            if selected_for_update is None
            else {label: [info for info in infos if id(info) in selected_for_update] for label, infos in groups.items()}
        )

        if not cfg.silent:
            all_infos = [info for infos in display_groups.values() for info in infos]
            if not cfg.all_deps and not any(info.is_shown(cfg.mode) or info.fetch_error for info in all_infos):
                continue

            from taze.ui.display import _age

            col_widths = (
                max((len(info.name) for info in all_infos), default=0),
                max((len(info.current_spec) for info in all_infos), default=0),
                max((len(_age(info.current_release_date)[0]) for info in all_infos), default=0),
                max((len(_age(info.release_date)[0]) for info in all_infos), default=0),
                max((len(info.latest_spec) for info in all_infos), default=0),
            )

            console.print(render_file_header(file_labels[file_path], all_infos, cfg.mode))
            console.print()

            display_groups = (
                display_groups
                if cfg.group
                else {"dependencies": [info for infos in display_groups.values() for info in infos]}
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

    if write:
        total_written = 0
        for file_path, groups in resolved.items():
            filtered = (
                {label: [info for info in infos if id(info) in selected_for_update] for label, infos in groups.items()}
                if selected_for_update is not None
                else groups
            )

            if is_action_file(file_path):
                updated = write_action_updates(
                    file_path,
                    [info for infos in filtered.values() for info in infos],
                    mode=cfg.mode,
                    style=github_actions_style,
                    pin_unchanged=cfg.github_actions_pin,
                )
            elif file_path.name == "pyproject.toml":
                updated = write_pyproject_updates(file_path, filtered, mode=cfg.mode)
            else:
                updated = write_requirements_updates(
                    file_path,
                    [info for infos in filtered.values() for info in infos],
                    mode=cfg.mode,
                )

            total_written += updated
            if updated and not cfg.silent:
                console.print(f"  [green]✓[/]  Wrote [bold]{updated}[/] update(s) to [cyan]{file_path.name}[/]")

        if total_written and not cfg.silent:
            console.print()
            if not install and not cfg.update and not cfg.interactive:
                command_text = " ".join(
                    install_command(next((file.parent for file in resolved if file.name == "pyproject.toml"), root))
                )
                console.print(f"  [dim]Run [cyan]{command_text}[/] to install the updates.[/]")
        if total_written and cfg.interactive and not cfg.silent and not install and not cfg.update:
            install = Confirm.ask("  [green]Install now?[/]", default=True, console=console)
            console.print()
    elif not cfg.silent:
        console.print(f"  [dim]Run [cyan]taze -w[/] to write {total_outdated} update(s)[/]")
        console.print()

    if install or cfg.update:
        install_cwd = next((file.parent for file in resolved if file.name == "pyproject.toml"), root)
        command = install_command(install_cwd)
        command_text = " ".join(command)
        if not cfg.silent:
            console.print(f"  [dim]Running [cyan]{command_text}[/]…[/]")
        result = subprocess.run(command, cwd=install_cwd, capture_output=cfg.silent, check=False)
        if result.returncode != 0:
            if not cfg.silent:
                console.print(f"[red]✗[/]  [bold]{command_text}[/] failed")
            raise typer.Exit(result.returncode)
        if not cfg.silent:
            console.print(f"  [green]✓[/]  [bold]{command_text}[/] complete")
            console.print()

    raise typer.Exit(1 if (cfg.fail_on_outdated and total_outdated) else 0)


def _count_outdated(resolved: dict[Path, dict[str, list[DepInfo]]], mode: str) -> int:
    return sum(1 for groups in resolved.values() for infos in groups.values() for info in infos if info.is_shown(mode))


def _path_patterns(value: object) -> tuple[str, ...]:
    """Normalise a comma-separated string or TOML list of glob patterns."""
    if isinstance(value, str):
        return tuple(pattern.strip() for pattern in value.split(",") if pattern.strip())
    if isinstance(value, list):
        return tuple(pattern for pattern in value if isinstance(pattern, str))
    return ()
