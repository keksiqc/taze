from __future__ import annotations

import json
import re
import subprocess
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import urllib.error
import urllib.request

import typer
from packaging.requirements import Requirement, InvalidRequirement
from packaging.version import Version, InvalidVersion
from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich import box
from rich.padding import Padding

# ─── App setup ────────────────────────────────────────────────────────────────

app = typer.Typer(
    name="taze",
    help="🥬 Keep your Python deps fresh",
    add_completion=False,
    rich_markup_mode="rich",
    no_args_is_help=False,
)

console = Console()

# ─── Data types ───────────────────────────────────────────────────────────────

BUMP_COLOR = {
    "major": "red",
    "minor": "yellow",
    "patch": "green",
    "same": "dim",
    "?": "dim",
}
BUMP_BADGE = {
    "major": "[bold red]MAJOR[/]",
    "minor": "[yellow]minor[/]",
    "patch": "[green]patch[/]",
    "same": "[dim]up to date[/]",
    "?": "[dim]?[/]",
}


@dataclass
class DepInfo:
    raw: str  # original dep string as it appears in TOML (without quotes)
    name: str  # normalised package name
    current: str | None  # version from the constraint (e.g. "2.28.0")
    operator: str | None  # e.g. ">=", "==", "~="
    latest: str | None = None
    bump: str = "?"
    fetch_error: bool = False

    # ── Display helpers ───────────────────────────────────────────────────────

    @property
    def current_spec(self) -> str:
        if self.operator and self.current:
            return f"{self.operator}{self.current}"
        return "(any)"

    @property
    def latest_spec(self) -> str:
        if not self.latest:
            return "—"
        if self.operator:
            if self.operator == "~=":
                # keep same number of version components
                n = len(self.current.split(".")) if self.current else 2
                parts = self.latest.split(".")[:n]
                return f"~={'.'.join(parts)}"
            return f"{self.operator}{self.latest}"
        return self.latest

    @property
    def is_outdated(self) -> bool:
        return self.bump not in ("same", "?") and not self.fetch_error

    # ── Build the updated dep string for writing back ─────────────────────────

    def updated_raw(self) -> str:
        if not self.latest or not self.operator:
            return self.raw
        try:
            req = Requirement(self.raw)
        except Exception:
            return self.raw

        new_specs: list[str] = []
        for spec in req.specifier:
            if spec.operator in (">=", "==", ">"):
                new_specs.append(f"{spec.operator}{self.latest}")
            elif spec.operator == "~=":
                n = len(spec.version.split("."))
                parts = self.latest.split(".")[:n]
                new_specs.append(f"~={'.'.join(parts)}")
            else:
                # keep upper-bound constraints (<, <=, !=) as-is
                new_specs.append(str(spec))

        extras = f"[{','.join(sorted(req.extras))}]" if req.extras else ""
        return f"{req.name}{extras}{','.join(new_specs)}"


# ─── PyPI fetching ────────────────────────────────────────────────────────────


def fetch_pypi_latest(package: str) -> str | None:
    """Return the latest stable version string for a package from PyPI."""
    try:
        url = f"https://pypi.org/pypi/{package}/json"
        request = urllib.request.Request(
            url, headers={"User-Agent": "taze/0.1.0 (https://github.com/your/taze)"}
        )
        with urllib.request.urlopen(request, timeout=10) as resp:
            data = json.loads(resp.read())

        # info.version is already the latest stable on PyPI
        candidate = data.get("info", {}).get("version", "")
        try:
            v = Version(candidate)
            if not v.is_prerelease and not v.is_devrelease:
                return str(v)
        except InvalidVersion:
            pass

        # Fallback: scan all release keys
        best: Version | None = None
        for v_str, files in data.get("releases", {}).items():
            if not files:  # yanked / no files
                continue
            try:
                v = Version(v_str)
            except InvalidVersion:
                continue
            if v.is_prerelease or v.is_devrelease:
                continue
            if best is None or v > best:
                best = v
        return str(best) if best else None

    except Exception:
        return None


# ─── Parsing ─────────────────────────────────────────────────────────────────


def parse_dep_string(raw: str) -> DepInfo | None:
    """Parse one PEP 508 dependency string into a DepInfo."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        req = Requirement(raw)
    except InvalidRequirement, Exception:
        return None

    name = req.name.lower().replace("_", "-")
    specs = list(req.specifier)
    current: str | None = None
    operator: str | None = None

    # Prefer the most meaningful lower-bound operator
    for op in ("==", ">=", "~=", ">"):
        for spec in specs:
            if spec.operator == op:
                current = spec.version
                operator = op
                break
        if current:
            break

    return DepInfo(raw=raw, name=name, current=current, operator=operator)


def parse_pyproject(path: Path) -> dict[str, list[str]]:
    """
    Return ordered dict: group_label → list of raw dep strings.

    Covers:
      • [project] dependencies
      • [project.optional-dependencies.*]
      • [dependency-groups.*]          (PEP 735, uv native)
      • [tool.uv.dev-dependencies]     (legacy uv format)
    """
    with open(path, "rb") as f:
        data = tomllib.load(f)

    groups: dict[str, list[str]] = {}

    project_deps: list = data.get("project", {}).get("dependencies", [])
    if project_deps:
        groups["dependencies"] = [d for d in project_deps if isinstance(d, str)]

    for grp, dep_list in (
        data.get("project", {}).get("optional-dependencies", {}).items()
    ):
        groups[f"optional:{grp}"] = [d for d in dep_list if isinstance(d, str)]

    for grp, dep_list in data.get("dependency-groups", {}).items():
        str_deps = [d for d in dep_list if isinstance(d, str)]
        if str_deps:
            groups[f"group:{grp}"] = str_deps

    uv_dev: list = data.get("tool", {}).get("uv", {}).get("dev-dependencies", [])
    if uv_dev:
        groups["dev-dependencies"] = [d for d in uv_dev if isinstance(d, str)]

    return groups


# ─── Version comparison ───────────────────────────────────────────────────────


def calc_bump(current: str | None, latest: str | None) -> str:
    if not current or not latest:
        return "?"
    try:
        c = Version(current)
        l = Version(latest)
        if l <= c:
            return "same"
        if l.major > c.major:
            return "major"
        if l.minor > c.minor:
            return "minor"
        return "patch"
    except InvalidVersion:
        return "?"


# ─── Core resolution ──────────────────────────────────────────────────────────


def resolve_group(
    dep_strings: list[str],
    include_filter: set[str] | None,
    exclude_filter: set[str],
    workers: int = 10,
) -> list[DepInfo]:
    """Parse + fetch latest for every dep in a group, concurrently."""
    infos: list[DepInfo] = []

    for raw in dep_strings:
        info = parse_dep_string(raw)
        if info is None:
            continue
        if include_filter is not None and info.name not in include_filter:
            continue
        if info.name in exclude_filter:
            continue
        infos.append(info)

    if not infos:
        return infos

    # Concurrent PyPI fetches
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_info = {
            pool.submit(fetch_pypi_latest, info.name): info for info in infos
        }
        for future in as_completed(future_to_info):
            info = future_to_info[future]
            try:
                info.latest = future.result()
                info.fetch_error = info.latest is None
            except Exception:
                info.fetch_error = True
            info.bump = calc_bump(info.current, info.latest)

    return infos


# ─── Display ─────────────────────────────────────────────────────────────────


def render_group(label: str, infos: list[DepInfo], show_up_to_date: bool) -> bool:
    """Render one dep group table. Returns True if anything was printed."""
    visible = [i for i in infos if show_up_to_date or i.is_outdated or i.fetch_error]
    if not visible:
        return False

    outdated_count = sum(1 for i in infos if i.is_outdated)
    total = len(infos)

    # Group header
    label_text = Text()
    label_text.append(f"  {label}", style="bold")
    if outdated_count:
        label_text.append(f"  {outdated_count} outdated", style="dim")
    else:
        label_text.append("  all up to date", style="dim green")
    console.print(label_text)

    table = Table(
        box=box.SIMPLE,
        show_header=False,
        padding=(0, 1),
        expand=False,
        show_edge=False,
    )
    table.add_column("name", style="bold", no_wrap=True, min_width=22)
    table.add_column("current", style="dim", no_wrap=True, min_width=14)
    table.add_column("arrow", no_wrap=True, min_width=3)
    table.add_column("latest", no_wrap=True, min_width=14)
    table.add_column("badge", no_wrap=True)

    for info in visible:
        color = BUMP_COLOR.get(info.bump, "dim")
        badge = BUMP_BADGE.get(info.bump, "")

        if info.fetch_error:
            table.add_row(
                info.name,
                info.current_spec,
                Text("→", style="dim"),
                Text("fetch failed", style="dim red"),
                "",
            )
        elif info.bump == "same":
            table.add_row(
                Text(info.name, style="dim"),
                Text(info.current_spec, style="dim"),
                Text("·", style="dim"),
                Text(info.current_spec, style="dim"),
                "",
            )
        else:
            table.add_row(
                info.name,
                Text(info.current_spec, style="dim"),
                Text("→", style=color),
                Text(info.latest_spec, style=f"bold {color}"),
                Text.from_markup(badge),
            )

    console.print(Padding(table, (0, 0, 0, 2)))
    return True


# ─── TOML writer ─────────────────────────────────────────────────────────────


def write_updates(path: Path, all_infos: dict[str, list[DepInfo]]) -> int:
    """Replace outdated dep strings in pyproject.toml. Returns number of updates."""
    content = path.read_text(encoding="utf-8")
    count = 0

    for _label, infos in all_infos.items():
        for info in infos:
            if not info.is_outdated:
                continue
            new_raw = info.updated_raw()
            if new_raw == info.raw:
                continue
            # Match the quoted dep string in TOML
            old_quoted = re.escape(f'"{info.raw}"')
            new_quoted = f'"{new_raw}"'
            new_content = re.sub(old_quoted, new_quoted, content)
            if new_content != content:
                content = new_content
                count += 1

    path.write_text(content, encoding="utf-8")
    return count


# ─── CLI entry point ──────────────────────────────────────────────────────────


@app.command()
def main(
    path: Path = typer.Argument(
        Path("."),
        help="Project directory or path to pyproject.toml",
        show_default=False,
    ),
    write: bool = typer.Option(
        False,
        "-w",
        "--write",
        help="Write updates back to pyproject.toml",
    ),
    install: bool = typer.Option(
        False,
        "-i",
        "--install",
        help="Run [cyan]uv sync[/] after writing (implies [cyan]--write[/])",
        rich_help_panel="Actions",
    ),
    group: Optional[str] = typer.Option(
        None,
        "-g",
        "--group",
        help="Only check a specific group (e.g. [cyan]dev[/], [cyan]optional:lint[/])",
    ),
    include: Optional[str] = typer.Option(
        None,
        "--include",
        help="Comma-separated package names (or /regex/) to include",
    ),
    exclude: Optional[str] = typer.Option(
        None,
        "--exclude",
        help="Comma-separated package names (or /regex/) to exclude",
    ),
    all_deps: bool = typer.Option(
        False,
        "-a",
        "--all",
        help="Show up-to-date packages too",
    ),
    workers: int = typer.Option(
        12,
        "--workers",
        help="Concurrent PyPI fetch workers",
        hidden=True,
    ),
) -> None:
    """
    🥬  [bold]taze[/bold] — keep your Python deps fresh

    Reads [cyan]pyproject.toml[/], checks PyPI for newer versions, and shows
    a grouped diff. Pass [cyan]-w[/] to write the bumped versions back, and
    [cyan]-i[/] to also run [cyan]uv sync[/].

    [dim]Examples:[/dim]
      [cyan]taze[/]                    # check everything
      [cyan]taze -w[/]                 # check and write updates
      [cyan]taze -w -i[/]              # write + uv sync
      [cyan]taze -g dev[/]             # only group:dev
      [cyan]taze --exclude pytest[/]   # skip pytest
    """
    if install:
        write = True

    # Resolve pyproject.toml path
    toml_path = path if path.name == "pyproject.toml" else path / "pyproject.toml"
    if not toml_path.exists():
        console.print(f"[red]✗[/]  [bold]pyproject.toml[/] not found at {toml_path}")
        raise typer.Exit(1)

    # Build include/exclude sets
    def _split_names(s: str | None) -> set[str] | None:
        if not s:
            return None
        return {n.strip().lower().replace("_", "-") for n in s.split(",") if n.strip()}

    include_filter = _split_names(include)
    exclude_filter = _split_names(exclude) or set()

    # Parse all dep groups from the TOML
    try:
        raw_groups = parse_pyproject(toml_path)
    except Exception as e:
        console.print(f"[red]✗[/]  Failed to parse pyproject.toml: {e}")
        raise typer.Exit(1)

    if not raw_groups:
        console.print("[yellow]No dependency sections found in pyproject.toml.[/]")
        raise typer.Exit(0)

    # Filter by --group if requested
    if group:
        # Accept bare name like "dev" → match "group:dev", "optional:dev", or "dev-dependencies"
        matched = {
            k: v
            for k, v in raw_groups.items()
            if k == group or k.endswith(f":{group}") or k == f"{group}-dependencies"
        }
        if not matched:
            available = ", ".join(raw_groups.keys())
            console.print(
                f"[red]✗[/]  Group [bold]{group!r}[/] not found. Available: {available}"
            )
            raise typer.Exit(1)
        raw_groups = matched

    # ── Header ──────────────────────────────────────────────────────────────
    console.print()
    console.print(f"  [bold]📦  pyproject.toml[/]  [dim]{toml_path.resolve()}[/]")
    console.print()

    # ── Fetch all groups concurrently ────────────────────────────────────────
    all_infos: dict[str, list[DepInfo]] = {}

    total_packages = sum(len(v) for v in raw_groups.values())
    with console.status(
        f"[dim]Checking {total_packages} packages on PyPI…[/]", spinner="dots"
    ):
        for label, dep_strings in raw_groups.items():
            all_infos[label] = resolve_group(
                dep_strings, include_filter, exclude_filter, workers
            )

    # ── Render ───────────────────────────────────────────────────────────────
    printed_any = False
    total_outdated = 0
    for label, infos in all_infos.items():
        if render_group(label, infos, all_deps):
            console.print()
            printed_any = True
        total_outdated += sum(1 for i in infos if i.is_outdated)

    if not printed_any:
        console.print("  [green]✓  All dependencies are up to date![/]")
        console.print()
        raise typer.Exit(0)

    if total_outdated == 0:
        raise typer.Exit(0)

    # ── Write ────────────────────────────────────────────────────────────────
    if write:
        updated = write_updates(toml_path, all_infos)
        console.print(
            f"  [green]✓[/]  Wrote [bold]{updated}[/] update(s) to "
            f"[cyan]{toml_path.name}[/]"
        )
        console.print()
    else:
        console.print(
            f"  [dim]Run [cyan]taze -w[/] to write {total_outdated} update(s) to pyproject.toml[/]"
        )
        console.print()

    # ── uv sync ──────────────────────────────────────────────────────────────
    if install:
        console.print("  [dim]Running [cyan]uv sync[/]…[/]")
        result = subprocess.run(
            ["uv", "sync"],
            cwd=toml_path.parent,
            capture_output=False,
        )
        if result.returncode != 0:
            console.print("[red]✗[/]  [bold]uv sync[/] failed")
            raise typer.Exit(result.returncode)
        console.print("  [green]✓[/]  [bold]uv sync[/] complete")
        console.print()


def run() -> None:
    app()


if __name__ == "__main__":
    run()
