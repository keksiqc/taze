from __future__ import annotations

from datetime import UTC, date, datetime

from rich import box
from rich.console import Console
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

from taze.models import BUMP_BADGE, BUMP_COLOR, BUMP_ORDER, DepInfo


console = Console()


def _age(release_date: str | None) -> tuple[str, str]:
    """Return compact release age text and its display color."""
    if not release_date:
        return "", "dim"
    try:
        days = (datetime.now(tz=UTC).date() - date.fromisoformat(release_date)).days
    except ValueError:
        return "", "dim"
    if days < 1:
        age = "~0d"
    elif days < 30:
        age = f"~{days}d"
    elif days < 365:
        age = f"~{days // 30}mo"
    else:
        age = f"~{days // 365}y"
    color = "green" if days < 28 else "yellow" if days < 180 else "red"
    return age, color


def render_group(
    label: str,
    infos: list[DepInfo],
    *,
    mode: str,
    show_up_to_date: bool,
    sort: str | None,
    col_widths: tuple[int, int, int, int, int] = (0, 0, 0, 0, 0),
) -> bool:
    """Render one dependency group table. Returns True if anything was printed."""
    visible = [i for i in infos if show_up_to_date or i.is_shown(mode) or i.fetch_error]
    if not visible:
        return False

    if sort:
        _sort_infos(visible, sort)

    outdated = sum(1 for i in infos if i.is_shown(mode))

    header = Text()
    header.append(f"  {label}", style="bold blue")
    if outdated:
        header.append(f"  {outdated} outdated", style="dim")
    else:
        header.append("  all up to date", style="dim green")
    console.print(header)

    name_width = max(max((len(i.name) for i in visible), default=0), col_widths[0])
    spec_width = max(max((len(i.current_spec) for i in visible), default=0), col_widths[1])
    cur_age_width = max(max((len(_age(i.current_release_date)[0]) for i in visible), default=0), col_widths[2])
    lat_age_width = max(max((len(_age(i.release_date)[0]) for i in visible), default=0), col_widths[3])
    latest_spec_width = max(max((len(i.latest_spec) for i in visible), default=0), col_widths[4])

    table = Table(
        box=box.SIMPLE,
        show_header=False,
        padding=(0, 2, 0, 0),
        expand=False,
        show_edge=False,
    )
    table.add_column("name", style="bold", no_wrap=True, min_width=name_width)
    table.add_column("cur_age", style="dim", no_wrap=True, min_width=cur_age_width)
    table.add_column("current", style="dim", no_wrap=True, min_width=spec_width)
    table.add_column("arrow", no_wrap=True)
    table.add_column("latest", no_wrap=True, min_width=latest_spec_width)
    table.add_column("lat_age", style="dim", no_wrap=True, min_width=lat_age_width)
    table.add_column("badge", no_wrap=True)

    for info in visible:
        color = BUMP_COLOR.get(info.bump, "dim")
        badge = BUMP_BADGE.get(info.bump, "")
        cur_age, cur_age_color = _age(info.current_release_date)
        lat_age, lat_age_color = _age(info.release_date)

        if info.fetch_error:
            table.add_row(
                info.name,
                "",
                info.current_spec,
                Text("→", style="dim"),
                Text("fetch failed", style="dim red"),
                "",
                "",
            )
        elif info.bump == "same":
            table.add_row(
                Text(info.name, style="dim"),
                Text(cur_age, style="dim"),
                Text(info.current_spec, style="dim"),
                Text("·", style="dim"),
                Text(info.current_spec, style="dim"),
                "",
                "",
            )
        else:
            table.add_row(
                info.name,
                Text(cur_age, style=cur_age_color),
                Text(info.current_spec, style="dim"),
                Text("→", style=color),
                Text(info.latest_spec, style=f"bold {color}"),
                Text(lat_age, style=lat_age_color),
                Text.from_markup(badge),
            )

    console.print(Padding(table, (0, 0, 0, 4)))
    return True


def _sort_infos(infos: list[DepInfo], sort: str) -> None:
    key = (lambda i: i.name) if sort.startswith("name") else lambda i: BUMP_ORDER.get(i.bump, -1)
    infos.sort(key=key, reverse=sort.endswith("desc"))


def render_json(
    resolved: dict[str, dict[str, list[DepInfo]]],
    *,
    mode: str = "major",
    show_up_to_date: bool = False,
) -> None:
    """Print agent-friendly JSON, hiding unchanged entries unless requested."""
    output: dict = {}
    for file_label, groups in resolved.items():
        output[file_label] = {}
        for group_label, infos in groups.items():
            output[file_label][group_label] = [
                {
                    "name": i.name,
                    "source": i.source,
                    "current": i.current,
                    "current_spec": i.current_spec,
                    "latest": i.latest,
                    "latest_spec": i.latest_spec if i.latest else None,
                    "bump": i.bump,
                    "outdated": i.is_outdated,
                    "release_date": i.release_date,
                    "current_release_date": i.current_release_date,
                    "error": i.fetch_error,
                }
                for i in infos
                if show_up_to_date or i.is_shown(mode) or i.fetch_error
            ]
    console.print_json(data=output)


def interactive_select(outdated: list[DepInfo]) -> list[DepInfo]:
    """Prompt the user to choose which packages to update. Returns selected subset."""
    if not outdated:
        return []

    console.print()
    console.print("  [bold]Select packages to update:[/]")
    for idx, info in enumerate(outdated, 1):
        color = BUMP_COLOR.get(info.bump, "dim")
        badge = BUMP_BADGE.get(info.bump, "")
        console.print(
            f"  [dim]{idx:>2}.[/]  [bold]{info.name}[/]  "
            f"[dim]{info.current_spec}[/] [dim]→[/] "
            f"[bold {color}]{info.latest_spec}[/]  {badge}",
        )
    console.print()
    console.print(
        "  [dim]Enter numbers (e.g. [cyan]1,3[/]), [cyan]a[/] for all, or press Enter to skip:[/] ",
        end="",
    )

    try:
        raw = input().strip()
    except EOFError, KeyboardInterrupt:
        console.print()
        return []

    if not raw or raw.lower() == "n":
        return []
    if raw.lower() in ("a", "all"):
        return outdated

    selected: list[DepInfo] = []
    for token in raw.split(","):
        lo, separator, hi = token.strip().partition("-")
        try:
            indices = range(int(lo), int(hi) + 1) if separator else (int(lo),)
        except ValueError:
            continue
        selected.extend(outdated[i - 1] for i in indices if 1 <= i <= len(outdated))

    return selected
