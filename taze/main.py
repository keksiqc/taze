from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Self, TypeVar, cast

import typer
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TaskID, TextColumn, TimeElapsedColumn

from taze.config import load_config, package_mode_for
from taze.discovery import discover_files
from taze.display import console, interactive_select, render_group, render_json
from taze.installers import install_command
from taze.models import MODE_SETTINGS, MODES, DepInfo, FileKind, calc_bump
from taze.parsers import (
    build_name_filter,
    parse_dep_string,
    parse_project_name,
    parse_pyproject,
    parse_requirements_file,
)
from taze.pypi import fetch_pypi_info
from taze.writers import write_pyproject_updates, write_requirements_updates


if TYPE_CHECKING:
    import re
    from collections.abc import Callable


T = TypeVar("T")


__version__ = "0.1.1"

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
    entries: list[tuple[str, Path | None, FileKind, int | None]],
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
) -> list[DepInfo]:
    """Fetch latest PyPI info for all deps and return enriched DepInfo list."""
    infos: list[DepInfo] = []
    for raw, src, kind, lineno in entries:
        info = parse_dep_string(raw, source_file=src, file_kind=kind, line_number=lineno)
        if info is None:
            continue
        if include_pat and not include_pat.match(info.name):
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

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(
                fetch_pypi_info,
                i.name,
                pre=pre,
                current_version=i.current,
                specifier=_resolution_specifier(i, mode=i.effective_mode or mode, include_locked=include_locked),
                mode=i.effective_mode or mode,
                maturity_period=0 if maturity_exclude_pat and maturity_exclude_pat.match(i.name) else maturity_period,
            ): i
            for i in infos
        }
        for fut in as_completed(futures):
            info = futures[fut]
            try:
                version, latest_date, current_date = fut.result()
                info.latest = version
                info.release_date = latest_date
                info.current_release_date = current_date
                info.fetch_error = version is None
            except Exception:
                info.fetch_error = True
            info.bump = calc_bump(info.current, info.latest)
            if on_progress is not None:
                on_progress(1)

    return infos


def _resolution_specifier(info: DepInfo, *, mode: str, include_locked: bool) -> SpecifierSet | None:
    """Return the declared PEP 440 range that applies to the selected mode."""
    if mode not in ("default", "stable") or (info.is_locked and include_locked):
        return None
    try:
        return Requirement(info.raw).specifier
    except Exception:
        return None


# ─── File discovery ───────────────────────────────────────────────────────────


# ─── CLI ──────────────────────────────────────────────────────────────────────


@app.command()
def main(
    context: typer.Context,
    mode: Annotated[
        str,
        typer.Argument(
            help=(
                "Update mode: "
                "[green]patch[/] [yellow]minor[/] [red]major[/] "
                "[dim]default | latest | stable | newest | next[/]"
            ),
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
        typer.Option("--group", help="Group dependencies by source file on display"),
    ] = False,
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
    ] = None,
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
    version: Annotated[
        bool,
        typer.Option("--version", "-v", help="Show version and exit", is_eager=True),
    ] = False,
    concurrency: Annotated[
        int,
        typer.Option("--concurrency", help="Number of concurrent PyPI requests"),
    ] = 10,
) -> None:
    """
    🥬  [bold]taze[/bold] — keep your Python deps fresh.

    Reads [cyan]pyproject.toml[/] and/or [cyan]requirements*.txt[/], checks PyPI for
    newer versions, and shows a grouped diff.

    [dim]Examples:[/dim]
      [cyan]taze[/]                       check everything (default mode)
      [cyan]taze minor[/]                 only show minor and patch updates
      [cyan]taze patch -w[/]              write patch updates back to file
      [cyan]taze newest -I[/]             interactive, including pre-releases
      [cyan]taze -r[/]                    scan subdirectories recursively
      [cyan]taze -x pytest,ruff[/]        skip specific packages
      [cyan]taze -n /^boto/[/]            only packages matching regex

      [cyan]taze --sort diff-desc[/]      biggest updates first
    """
    if version:
        console.print(f"taze/{__version__}")
        raise typer.Exit(0)

    if mode not in MODES:
        console.print(f"[red]✗[/]  Unknown mode [bold]{mode!r}[/]. Available: {' | '.join(MODES)}")
        raise typer.Exit(1)

    if sort and sort not in SORT_CHOICES:
        console.print(f"[red]✗[/]  --sort must be one of: {', '.join(SORT_CHOICES)}")
        raise typer.Exit(1)

    if install or update:
        write = True
    if interactive:
        write = True

    _, pre = MODE_SETTINGS[mode]

    root = (cwd or Path()).resolve()
    config_path = (root / config).resolve() if config and not config.is_absolute() else config
    project_config = load_config(root, config_path)
    include = _configured(context, "include", include, project_config)
    exclude = _configured(context, "exclude", exclude, project_config)
    recursive = _configured(context, "recursive", recursive, project_config)
    ignore_paths = _configured(context, "ignore_paths", ignore_paths, project_config)
    ignore_other_workspaces = _configured(context, "ignore_other_workspaces", ignore_other_workspaces, project_config)
    include_locked = _configured(context, "include_locked", include_locked, project_config)
    concurrency = _configured(context, "concurrency", concurrency, project_config)
    maturity_period = _configured(context, "maturity_period", maturity_period, project_config)
    maturity_period_exclude = _configured(
        context,
        "maturity_period_exclude",
        maturity_period_exclude,
        project_config,
    )
    package_modes = project_config.get("package_mode", {})
    include_pat = build_name_filter(include) if include else None
    exclude_pat = build_name_filter(exclude) if exclude else None
    maturity_exclude_pat = build_name_filter(maturity_period_exclude) if maturity_period_exclude else None

    # ── Collect files ─────────────────────────────────────────────────────────
    ignored = _path_patterns(ignore_paths)
    target_files = discover_files(
        root,
        recursive=recursive,
        ignore_paths=ignored,
        ignore_other_workspaces=ignore_other_workspaces,
    )

    if not target_files:
        if not silent:
            console.print(f"[red]✗[/]  No pyproject.toml or requirements*.txt found in {root}")
        raise typer.Exit(1)

    local_package_names: set[str] = set()
    for file_path in target_files:
        if file_path.name != "pyproject.toml":
            continue
        try:
            name = parse_project_name(file_path)
        except Exception:
            name = None
        if name:
            local_package_names.add(name)

    # ── Build entries per file ────────────────────────────────────────────────
    # file_path → group_label → raw entries
    raw_file_groups: dict[Path, dict[str, list[tuple[str, Path | None, FileKind, int | None]]]] = {}

    for file_path in target_files:
        if file_path.name == "pyproject.toml":
            try:
                raw_groups = parse_pyproject(file_path)
            except Exception as e:
                if not silent:
                    console.print(f"[red]✗[/]  Failed to parse {file_path}: {e}")
                continue
            raw_file_groups[file_path] = {
                label: [(s, file_path, FileKind.PYPROJECT, None) for s in deps] for label, deps in raw_groups.items()
            }
        else:
            try:
                pairs = parse_requirements_file(file_path)
            except Exception as e:
                if not silent:
                    console.print(f"[red]✗[/]  Failed to parse {file_path}: {e}")
                continue
            raw_file_groups[file_path] = {
                "requirements": [(s, file_path, FileKind.REQUIREMENTS, ln) for ln, s in pairs],
            }

    if not raw_file_groups:
        raise typer.Exit(1)

    total_packages = sum(len(entries) for groups in raw_file_groups.values() for entries in groups.values())

    # ── Resolve (fetch PyPI) ──────────────────────────────────────────────────
    resolved: dict[Path, dict[str, list[DepInfo]]] = {}

    progress_ctx = (
        Progress(
            TextColumn("[dim]Checking packages on PyPI…[/]"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        )
        if not silent
        else _NullCtx()
    )
    with progress_ctx as _progress:
        task_id = _progress.add_task("checking", total=total_packages)

        def on_progress(n: int) -> None:
            if not silent:
                _progress.update(task_id, advance=n)

        for file_path, groups in raw_file_groups.items():
            resolved[file_path] = {}
            for label, entries in groups.items():
                resolved[file_path][label] = resolve_deps(
                    entries,
                    include_pat=include_pat,
                    exclude_pat=exclude_pat,
                    pre=pre,
                    mode=mode,
                    include_locked=include_locked,
                    maturity_period=maturity_period,
                    maturity_exclude_pat=maturity_exclude_pat,
                    package_modes=package_modes,
                    local_package_names=local_package_names,
                    concurrency=concurrency,
                    on_progress=on_progress,
                )

    # ── JSON output ───────────────────────────────────────────────────────────
    if output_json:
        render_json({str(fp): grps for fp, grps in resolved.items()})
        total_outdated = _count_outdated(resolved, mode)
        raise typer.Exit(1 if (fail_on_outdated and total_outdated) else 0)

    # ── Rich display ──────────────────────────────────────────────────────────
    if not silent:
        console.print()

    total_outdated = 0

    for file_path, groups in resolved.items():
        file_outdated = _count_outdated({file_path: groups}, mode)
        total_outdated += file_outdated

        if not silent:
            # Compute column widths across all groups in this file so every
            # group aligns to the same grid.
            all_infos = [i for infos in groups.values() for i in infos]
            from taze.display import _age

            col_widths = (
                max((len(i.name) for i in all_infos), default=0),
                max((len(i.current_spec) for i in all_infos), default=0),
                max((len(_age(i.current_release_date)) for i in all_infos), default=0),
                max((len(_age(i.release_date)) for i in all_infos), default=0),
                max((len(i.latest_spec) for i in all_infos), default=0),
            )

            console.print(f"  [bold]📦  {file_path.name}[/]  [dim]{file_path.resolve()}[/]")
            console.print()

            display_groups = groups if group else {"dependencies": [i for infos in groups.values() for i in infos]}
            for label, infos in display_groups.items():
                if render_group(
                    label,
                    infos,
                    mode=mode,
                    show_up_to_date=all_deps,
                    sort=sort,
                    col_widths=col_widths,
                ):
                    console.print()

            if file_outdated == 0:
                console.print("  [green]✓  All dependencies are up to date![/]")
                console.print()

    if total_outdated == 0:
        raise typer.Exit(0)

    # ── Interactive selection ─────────────────────────────────────────────────
    selected_for_update: set[str] | None = None  # None = all

    if interactive and not silent:
        all_outdated = [
            i for groups in resolved.values() for infos in groups.values() for i in infos if i.is_shown(mode)
        ]
        chosen = interactive_select(all_outdated)
        selected_for_update = {i.name for i in chosen}
        console.print()

    # ── Write ─────────────────────────────────────────────────────────────────
    if write:
        total_written = 0
        for file_path, groups in resolved.items():
            # Filter to selected packages if in interactive mode
            if selected_for_update is not None:
                filtered: dict[str, list[DepInfo]] = {
                    label: [i for i in infos if i.name in selected_for_update] for label, infos in groups.items()
                }
            else:
                filtered = groups

            if file_path.name == "pyproject.toml":
                updated = write_pyproject_updates(file_path, filtered, mode=mode)
            else:
                flat = [i for infos in filtered.values() for i in infos]
                updated = write_requirements_updates(file_path, flat, mode=mode)

            if updated and not silent:
                console.print(f"  [green]✓[/]  Wrote [bold]{updated}[/] update(s) to [cyan]{file_path.name}[/]")
                total_written += updated

        if total_written and not silent:
            console.print()
    elif not silent:
        console.print(f"  [dim]Run [cyan]taze -w[/] to write {total_outdated} update(s)[/]")
        console.print()

    # ── Prompt to install after -w (unless -i/-u already set) ───────────────
    if write and not install and not update and not silent and total_written > 0:
        command_text = " ".join(
            install_command(next((fp.parent for fp in resolved if fp.name == "pyproject.toml"), root))
        )
        console.print(f"  [dim]Run [cyan]{command_text}[/] now? [bold](y/N)[/] [/]", end="")
        try:
            answer = input().strip().lower()
        except EOFError, KeyboardInterrupt:
            answer = ""
            console.print()
        if answer == "y":
            install = True

    # ── Lockfile-aware install ───────────────────────────────────────────────
    if install or update:
        install_cwd = next(
            (fp.parent for fp in resolved if fp.name == "pyproject.toml"),
            root,
        )
        command = install_command(install_cwd)
        command_text = " ".join(command)
        if not silent:
            console.print(f"  [dim]Running [cyan]{command_text}[/]…[/]")
        result = subprocess.run(  # noqa: S603 -- command comes from the fixed installer registry
            command,
            cwd=install_cwd,
            capture_output=silent,
        )
        if result.returncode != 0:
            if not silent:
                console.print(f"[red]✗[/]  [bold]{command_text}[/] failed")
            raise typer.Exit(result.returncode)
        if not silent:
            console.print(f"  [green]✓[/]  [bold]{command_text}[/] complete")
            console.print()

    raise typer.Exit(1 if (fail_on_outdated and total_outdated) else 0)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _count_outdated(resolved: dict[Path, dict[str, list[DepInfo]]], mode: str) -> int:
    return sum(1 for groups in resolved.values() for infos in groups.values() for i in infos if i.is_shown(mode))


def _configured(context: typer.Context, name: str, current: T, config: dict[str, object]) -> T:
    """Use the project setting only when the corresponding CLI option was omitted."""
    if name not in config:
        return current
    try:
        source = context.get_parameter_source(name)
    except AttributeError:
        return current
    return cast(T, config[name]) if source and source.name == "DEFAULT" else current


def _path_patterns(value: object) -> tuple[str, ...]:
    """Normalise a comma-separated string or TOML list of glob patterns."""
    if isinstance(value, str):
        return tuple(p.strip() for p in value.split(",") if p.strip())
    if isinstance(value, list):
        return tuple(p for p in value if isinstance(p, str))
    return ()


class _NullCtx:
    """No-op context manager (replaces console.status when --silent)."""

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def add_task(self, _description: str, *, total: int) -> TaskID:
        return TaskID(0)

    def update(self, _task_id: TaskID, *, advance: int) -> None:
        pass


# ─── Entry point ──────────────────────────────────────────────────────────────


def run() -> None:
    """Entry point for the taze CLI."""
    app()


if __name__ == "__main__":
    run()
