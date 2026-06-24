from __future__ import annotations

import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Annotated

import typer

from .display import console, interactive_select, render_group, render_json
from .models import MODE_SETTINGS, MODES, DepInfo, FileKind, calc_bump
from .parsers import (
    build_name_filter,
    parse_dep_string,
    parse_pyproject,
    parse_requirements_file,
)
from .pypi import fetch_pypi_info
from .writers import write_pyproject_updates, write_requirements_updates

__version__ = "0.1.0"

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
    concurrency: int,
) -> list[DepInfo]:
    infos: list[DepInfo] = []
    for raw, src, kind, lineno in entries:
        info = parse_dep_string(
            raw, source_file=src, file_kind=kind, line_number=lineno
        )
        if info is None:
            continue
        if include_pat and not include_pat.match(info.name):
            continue
        if exclude_pat and exclude_pat.match(info.name):
            continue
        infos.append(info)

    if not infos:
        return infos

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(fetch_pypi_info, i.name, pre=pre, current_version=i.current): i
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

    return infos


# ─── File discovery ───────────────────────────────────────────────────────────


def discover_files(root: Path) -> list[Path]:
    found: list[Path] = []
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        found.append(pyproject)
    for req in sorted(root.glob("requirements*.txt")):
        found.append(req)
    return found


# ─── CLI ──────────────────────────────────────────────────────────────────────


@app.command()
def main(
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
    write: Annotated[
        bool, typer.Option("--write", "-w", help="Write updates back to file")
    ] = False,
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
    all_deps: Annotated[
        bool, typer.Option("--all", "-a", help="Show up-to-date packages too")
    ] = False,
    group: Annotated[
        bool,
        typer.Option("--group", help="Group dependencies by source file on display"),
    ] = False,
    sort: Annotated[
        str | None,
        typer.Option(
            "--sort", help="Sort by: name-asc | name-desc | diff-asc | diff-desc"
        ),
    ] = None,
    fail_on_outdated: Annotated[
        bool,
        typer.Option(
            "--fail-on-outdated",
            help="Exit with code 1 if outdated dependencies are found",
        ),
    ] = False,
    silent: Annotated[bool, typer.Option("--silent", "-s", help="No output")] = False,
    output_json: Annotated[
        bool, typer.Option("--json", help="Machine-readable JSON output", hidden=True)
    ] = False,
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
    🥬  [bold]taze[/bold] — keep your Python deps fresh

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
        console.print(
            f"[red]✗[/]  Unknown mode [bold]{mode!r}[/]. Available: {' | '.join(MODES)}"
        )
        raise typer.Exit(1)

    if sort and sort not in SORT_CHOICES:
        console.print(f"[red]✗[/]  --sort must be one of: {', '.join(SORT_CHOICES)}")
        raise typer.Exit(1)

    if install or update:
        write = True
    if interactive:
        write = True

    _, pre = MODE_SETTINGS[mode]

    root = (cwd or Path(".")).resolve()
    include_pat = build_name_filter(include) if include else None
    exclude_pat = build_name_filter(exclude) if exclude else None

    # ── Collect files ─────────────────────────────────────────────────────────
    if recursive:
        target_files: list[Path] = []
        seen: set[Path] = set()
        for subdir in sorted(root.rglob(".")):
            for f in discover_files(subdir):
                if f not in seen:
                    seen.add(f)
                    target_files.append(f)
    else:
        target_files = discover_files(root)

    if not target_files:
        if not silent:
            console.print(
                f"[red]✗[/]  No pyproject.toml or requirements*.txt found in {root}"
            )
        raise typer.Exit(1)

    # ── Build entries per file ────────────────────────────────────────────────
    # file_path → group_label → raw entries
    raw_file_groups: dict[
        Path, dict[str, list[tuple[str, Path | None, FileKind, int | None]]]
    ] = {}

    for file_path in target_files:
        if file_path.name == "pyproject.toml":
            try:
                raw_groups = parse_pyproject(file_path)
            except Exception as e:
                if not silent:
                    console.print(f"[red]✗[/]  Failed to parse {file_path}: {e}")
                continue
            raw_file_groups[file_path] = {
                label: [(s, file_path, FileKind.PYPROJECT, None) for s in deps]
                for label, deps in raw_groups.items()
            }
        else:
            try:
                pairs = parse_requirements_file(file_path)
            except Exception as e:
                if not silent:
                    console.print(f"[red]✗[/]  Failed to parse {file_path}: {e}")
                continue
            raw_file_groups[file_path] = {
                "requirements": [
                    (s, file_path, FileKind.REQUIREMENTS, ln) for ln, s in pairs
                ]
            }

    if not raw_file_groups:
        raise typer.Exit(1)

    total_packages = sum(
        len(entries)
        for groups in raw_file_groups.values()
        for entries in groups.values()
    )

    # ── Resolve (fetch PyPI) ──────────────────────────────────────────────────
    resolved: dict[Path, dict[str, list[DepInfo]]] = {}

    status_msg = f"[dim]Checking {total_packages} package(s) on PyPI…[/]"
    with console.status(status_msg, spinner="dots") if not silent else _nullctx():
        for file_path, groups in raw_file_groups.items():
            resolved[file_path] = {}
            for label, entries in groups.items():
                resolved[file_path][label] = resolve_deps(
                    entries,
                    include_pat=include_pat,
                    exclude_pat=exclude_pat,
                    pre=pre,
                    concurrency=concurrency,
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
            from .display import _age

            col_widths = (
                max((len(i.name) for i in all_infos), default=0),
                max((len(i.current_spec) for i in all_infos), default=0),
                max((len(_age(i.current_release_date)) for i in all_infos), default=0),
                max((len(_age(i.release_date)) for i in all_infos), default=0),
                max((len(i.latest_spec) for i in all_infos), default=0),
            )

            console.print(
                f"  [bold]📦  {file_path.name}[/]  [dim]{file_path.resolve()}[/]"
            )
            console.print()

            for label, infos in groups.items():
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
            i
            for groups in resolved.values()
            for infos in groups.values()
            for i in infos
            if i.is_shown(mode)
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
                    label: [i for i in infos if i.name in selected_for_update]
                    for label, infos in groups.items()
                }
            else:
                filtered = groups

            if file_path.name == "pyproject.toml":
                updated = write_pyproject_updates(file_path, filtered)
            else:
                flat = [i for infos in filtered.values() for i in infos]
                updated = write_requirements_updates(file_path, flat)

            if updated and not silent:
                console.print(
                    f"  [green]✓[/]  Wrote [bold]{updated}[/] update(s) to "
                    f"[cyan]{file_path.name}[/]"
                )
                total_written += updated

        if total_written and not silent:
            console.print()
    elif not silent:
        console.print(
            f"  [dim]Run [cyan]taze -w[/] to write {total_outdated} update(s)[/]"
        )
        console.print()

    # ── Prompt to install after -w (unless -i/-u already set) ───────────────
    if write and not install and not update and not silent and total_written > 0:
        console.print("  [dim]Run [cyan]uv sync[/] now? [bold](y/N)[/] [/]", end="")
        try:
            answer = input().strip().lower()
        except EOFError, KeyboardInterrupt:
            answer = ""
            console.print()
        if answer == "y":
            install = True

    # ── uv sync / install ────────────────────────────────────────────────────
    if install or update:
        uv_cwd = next(
            (fp.parent for fp in resolved if fp.name == "pyproject.toml"),
            root,
        )
        if not silent:
            console.print("  [dim]Running [cyan]uv sync[/]…[/]")
        result = subprocess.run(
            ["uv", "sync"],
            cwd=uv_cwd,
            capture_output=silent,
        )
        if result.returncode != 0:
            if not silent:
                console.print("[red]✗[/]  [bold]uv sync[/] failed")
            raise typer.Exit(result.returncode)
        if not silent:
            console.print("  [green]✓[/]  [bold]uv sync[/] complete")
            console.print()

    raise typer.Exit(1 if (fail_on_outdated and total_outdated) else 0)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _count_outdated(resolved: dict[Path, dict[str, list[DepInfo]]], mode: str) -> int:
    return sum(
        1
        for groups in resolved.values()
        for infos in groups.values()
        for i in infos
        if i.is_shown(mode)
    )


class _nullctx:
    """No-op context manager (replaces console.status when --silent)."""

    def __enter__(self) -> _nullctx:
        return self

    def __exit__(self, *_: object) -> None:
        pass


# ─── Entry point ──────────────────────────────────────────────────────────────


def run() -> None:
    app()


if __name__ == "__main__":
    run()
