"""End-to-end dependency check workflow."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

import typer
from packaging.version import Version
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.prompt import Confirm

from taze.config import TazeConfig
from taze.core.resolution import ResolvePolicy, resolve_deps
from taze.io.actions import is_action_file, is_pinnable, parse_actions, write_action_updates
from taze.io.cache import load_cache, save_cache
from taze.io.discovery import discover_files
from taze.io.installers import install_command
from taze.io.parsers import parse_project_name, parse_pyproject_deps, parse_requirements, parse_requires_python
from taze.io.writers import write_pyproject_updates, write_requirements_updates
from taze.models import MODES, DepInfo
from taze.registries import github
from taze.registries.pypi import minimum_python
from taze.ui.display import console, error_console, interactive_select, render_file, render_json


SORT_CHOICES = ("name-asc", "name-desc", "diff-asc", "diff-desc")
ACTION_STYLES = ("auto", "tag", "sha")

FileGroups = dict[str, list[DepInfo]]
Resolved = dict[Path, FileGroups]


@dataclass(frozen=True)
class RunOptions:
    """Validated, derived run settings (``-i`` implies ``-w``, ``--github-actions-pin`` implies ``sha``)."""

    write: bool
    install: bool
    action_style: str


@dataclass
class Workspace:
    """Dependency files found under the root, plus what the enclosing projects declare."""

    groups: Resolved = field(default_factory=dict)
    local_package_names: set[str] = field(default_factory=set)
    project_pythons: dict[Path, Version] = field(default_factory=dict)

    def python_for(self, file_path: Path) -> Version | None:
        """The ``requires-python`` floor of the nearest enclosing project, if any."""
        for directory in (file_path.parent, *file_path.parent.parents):
            if directory in self.project_pythons:
                return self.project_pythons[directory]
        return None

    @property
    def package_count(self) -> int:
        return sum(len(infos) for groups in self.groups.values() for infos in groups.values())


def run(root: Path, cfg: TazeConfig, *, no_retry: bool = False) -> None:
    """Run one dependency check from discovery through optional installation."""
    options = _validate(cfg)
    workspace = _collect(root, cfg)
    try:
        policy = ResolvePolicy.from_config(
            cfg,
            local_package_names=frozenset(workspace.local_package_names),
            github_actions_style=options.action_style,
            retries=0 if no_retry else None,
        )
    except re.error as error:
        _fail(f"Invalid dependency filter: {error}")
    resolved = _resolve(workspace, policy, cfg)

    if cfg.output_json:
        render_json(
            {str(path): groups for path, groups in resolved.items()}, mode=cfg.mode, show_up_to_date=cfg.all_deps
        )
        if options.write:
            _write_all(resolved, cfg, options, quiet=True)
        _exit(cfg, _count_outdated(resolved, cfg.mode))

    total_outdated = _count_outdated(resolved, cfg.mode)
    if cfg.interactive and not cfg.silent:
        resolved = _select_interactively(resolved, cfg, _file_labels(resolved, root))
        total_outdated = _count_outdated(resolved, cfg.mode)
        console.print()
        if total_outdated == 0:
            raise typer.Exit(0)

    if total_outdated == 0 and not _has_pinnable_actions(resolved, cfg, options):
        if not cfg.silent:
            console.print("[green]dependencies are already up-to-date[/]")
        raise typer.Exit(0)

    write, install = options.write, options.install
    if not cfg.silent:
        console.print()
        _render_all(resolved, cfg, _file_labels(resolved, root))
        if cfg.interactive and not write:
            write = Confirm.ask("  [green]Write updates?[/]", default=True, console=console)
            console.print()

    if write:
        written = _write_all(resolved, cfg, options)
        if written and not cfg.silent:
            console.print()
            if cfg.interactive and not install:
                install = Confirm.ask("  [green]Install now?[/]", default=True, console=console)
                console.print()
            elif not install:
                command = " ".join(install_command(_install_dir(resolved, root)))
                console.print(f"  [dim]Run [cyan]{command}[/] to install the updates.[/]")
    elif not cfg.silent:
        console.print(f"  [dim]Run [cyan]taze -w[/] to write {total_outdated} update(s)[/]")
        console.print()

    if install:
        _install(_install_dir(resolved, root), silent=cfg.silent)

    _exit(cfg, total_outdated)


# --- phases -----------------------------------------------------------------


def _validate(cfg: TazeConfig) -> RunOptions:
    if cfg.mode not in MODES:
        _fail(f"Unknown mode [bold]{cfg.mode!r}[/]. Available: {' | '.join(MODES)}")
    if cfg.sort and cfg.sort not in SORT_CHOICES:
        _fail(f"--sort must be one of: {', '.join(SORT_CHOICES)}")
    if cfg.github_actions_style not in ACTION_STYLES:
        _fail("--github-actions-style must be auto, tag, or sha")
    if cfg.concurrency < 1 or cfg.request_timeout <= 0 or cfg.retries < 0 or cfg.maturity_period < 0:
        _fail("concurrency, timeout, and maturity-period must be positive; retries cannot be negative")
    style = "sha" if cfg.github_actions_pin and cfg.github_actions_style == "auto" else cfg.github_actions_style
    install = cfg.install or cfg.update
    return RunOptions(write=cfg.write or install or cfg.github_actions_pin, install=install, action_style=style)


def _collect(root: Path, cfg: TazeConfig) -> Workspace:
    """Discover and parse every supported file. Exits when nothing usable was found."""
    files = discover_files(
        root,
        recursive=cfg.recursive,
        ignore_paths=_path_patterns(cfg.ignore_paths),
        ignore_other_workspaces=cfg.ignore_other_workspaces,
        github_actions=cfg.github_actions,
    )
    if not files:
        _fail(f"No supported dependency files found in {root}", silent=cfg.silent)

    workspace = Workspace()
    for path in files:
        if path.name != "pyproject.toml":
            continue
        try:
            name = parse_project_name(path)
            python = minimum_python(parse_requires_python(path))
        except (AttributeError, OSError, TypeError, ValueError):
            continue
        if name:
            workspace.local_package_names.add(name)
        if python:
            workspace.project_pythons[path.parent] = python

    for path in files:
        try:
            groups = _parse_file(path)
        except (AttributeError, OSError, TypeError, UnicodeError, ValueError) as error:
            if not cfg.silent:
                error_console.print(f"[red]✗[/]  Failed to parse {path}: {error}")
            continue
        if groups is not None:
            workspace.groups[path] = groups

    if not workspace.groups:
        raise typer.Exit(1)
    return workspace


def _parse_file(path: Path) -> FileGroups | None:
    if path.name == "pyproject.toml":
        return parse_pyproject_deps(path)
    if is_action_file(path):
        infos = parse_actions(path)
        return {"github-actions": infos} if infos else None
    return {"requirements": parse_requirements(path)}


def _resolve(workspace: Workspace, policy: ResolvePolicy, cfg: TazeConfig) -> Resolved:
    cache = load_cache(force=cfg.force)
    resolved: Resolved = {}
    with Progress(
        TextColumn("[dim]Checking packages on registries…[/]"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
        disable=cfg.silent or cfg.output_json,
    ) as progress:
        task_id = progress.add_task("checking", total=workspace.package_count)
        for path, groups in workspace.groups.items():
            resolved[path] = {
                label: resolve_deps(
                    infos,
                    policy,
                    cache=cache,
                    python_version=workspace.python_for(path),
                    on_progress=lambda n: progress.update(task_id, advance=n),
                )
                for label, infos in groups.items()
            }
    save_cache(cache)
    if github.rate_limit_hit and not cfg.silent:
        error_console.print(
            "[yellow]![/]  GitHub API rate limit reached; set [cyan]GITHUB_TOKEN[/] or run [cyan]gh auth login[/] "
            "to check actions."
        )
    return resolved


def _select_interactively(resolved: Resolved, cfg: TazeConfig, labels: dict[Path, str]) -> Resolved:
    """Let the user pick from the outdated entries; returns only what they chose."""
    categories: list[tuple[str, list[tuple[str, list[DepInfo]]]]] = []
    candidates: list[DepInfo] = []
    for path, groups in resolved.items():
        sections = groups.items() if cfg.group else [("dependencies", _flatten(groups))]
        file_groups: list[tuple[str, list[DepInfo]]] = []
        for label, infos in sections:
            outdated = [info for info in infos if info.is_shown(cfg.mode) and not info.fetch_error]
            if outdated:
                file_groups.append((label, outdated))
                candidates.extend(outdated)
        if file_groups:
            categories.append((labels[path], file_groups))
    chosen = {id(info) for info in interactive_select(candidates, categories)}
    return {
        path: {label: [info for info in infos if id(info) in chosen] for label, infos in groups.items()}
        for path, groups in resolved.items()
    }


def _render_all(resolved: Resolved, cfg: TazeConfig, labels: dict[Path, str]) -> None:
    for path, groups in resolved.items():
        render_file(labels[path], groups, mode=cfg.mode, show_up_to_date=cfg.all_deps, sort=cfg.sort, grouped=cfg.group)


def _write_all(resolved: Resolved, cfg: TazeConfig, options: RunOptions, *, quiet: bool = False) -> int:
    total = 0
    for path, groups in resolved.items():
        written = _write_file(path, groups, cfg, options)
        total += written
        if written and not quiet and not cfg.silent:
            console.print(f"  [green]✓[/]  Wrote [bold]{written}[/] update(s) to [cyan]{path.name}[/]")
    return total


def _write_file(path: Path, groups: FileGroups, cfg: TazeConfig, options: RunOptions) -> int:
    """Dispatch to the writer that understands this file's format. Returns the number of updates."""
    if is_action_file(path):
        return write_action_updates(
            path, _flatten(groups), mode=cfg.mode, style=options.action_style, pin_unchanged=cfg.github_actions_pin
        )
    if path.name == "pyproject.toml":
        return write_pyproject_updates(path, groups, mode=cfg.mode)
    return write_requirements_updates(path, _flatten(groups), mode=cfg.mode)


def _install(directory: Path, *, silent: bool) -> None:
    command = install_command(directory)
    command_text = " ".join(command)
    if not silent:
        console.print(f"  [dim]Running [cyan]{command_text}[/]…[/]")
    result = subprocess.run(command, cwd=directory, capture_output=silent, check=False)
    if result.returncode != 0:
        _fail(f"[bold]{command_text}[/] failed", silent=silent, code=result.returncode)
    if not silent:
        console.print(f"  [green]✓[/]  [bold]{command_text}[/] complete")
        console.print()


# --- helpers ----------------------------------------------------------------


def _fail(message: str, *, silent: bool = False, code: int = 1) -> NoReturn:
    if not silent:
        error_console.print(f"[red]✗[/]  {message}")
    raise typer.Exit(code)


def _exit(cfg: TazeConfig, total_outdated: int) -> NoReturn:
    raise typer.Exit(1 if (cfg.fail_on_outdated and total_outdated) else 0)


def _flatten(groups: FileGroups) -> list[DepInfo]:
    return [info for infos in groups.values() for info in infos]


def _count_outdated(resolved: Resolved, mode: str) -> int:
    return sum(1 for groups in resolved.values() for info in _flatten(groups) if info.is_shown(mode))


def _has_pinnable_actions(resolved: Resolved, cfg: TazeConfig, options: RunOptions) -> bool:
    if not cfg.github_actions_pin:
        return False
    return any(
        info.action_target_sha and is_pinnable(info, options.action_style)
        for groups in resolved.values()
        for info in _flatten(groups)
    )


def _install_dir(resolved: Resolved, root: Path) -> Path:
    return next((path.parent for path in resolved if path.name == "pyproject.toml"), root)


def _file_labels(resolved: Resolved, root: Path) -> dict[Path, str]:
    """Short names when unique, root-relative paths when the same filename appears more than once."""
    counts: dict[str, int] = {}
    for path in resolved:
        counts[path.name] = counts.get(path.name, 0) + 1
    labels: dict[Path, str] = {}
    for path in resolved:
        if counts[path.name] == 1:
            labels[path] = path.name
        else:
            try:
                labels[path] = str(path.relative_to(root))
            except ValueError:
                labels[path] = str(path)
    return labels


def _path_patterns(value: object) -> tuple[str, ...]:
    """Normalise a comma-separated string or TOML list of glob patterns."""
    if isinstance(value, str):
        return tuple(pattern.strip() for pattern in value.split(",") if pattern.strip())
    if isinstance(value, list):
        return tuple(pattern for pattern in value if isinstance(pattern, str))
    return ()
