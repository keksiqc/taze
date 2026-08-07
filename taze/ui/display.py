"""Render dependency updates in the terminal."""

from __future__ import annotations

import re
import select
import shutil
import sys
from datetime import UTC, date, datetime

from rich import box
from rich.console import Console
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

from taze.models import BUMP_COLOR, BUMP_ORDER, DepInfo, calc_bump


console = Console()


def _age(release_date: str | None) -> tuple[str, str]:
    """Return compact release age text and its display color, mirroring upstream's timeDifference."""
    if not release_date:
        return "", "dim"
    try:
        days = (datetime.now(tz=UTC).date() - date.fromisoformat(release_date)).days
    except ValueError:
        return "", "dim"
    if days < 30:
        return f"~{days}d", "green"
    if days < 365:
        return f"~{days // 30}mo", "yellow"
    return f"~{days / 365:.1f}y", "red"


def _split_leading_operator(spec: str) -> tuple[str, str]:
    """Split a specifier like '>=1.2.3' into its operator prefix and numeric body."""
    index = 0
    while index < len(spec) and not spec[index].isdigit():
        index += 1
    return spec[:index], spec[index:]


def _colorize_diff(current_spec: str, latest_spec: str, color: str) -> Text:
    """Highlight only the version segment that changed, like upstream taze's colorizeVersionDiff."""
    cur_op, cur_ver = _split_leading_operator(current_spec)
    lat_op, lat_ver = _split_leading_operator(latest_spec)
    if not lat_ver:
        return Text(latest_spec, style=f"bold {color}")

    cur_parts = cur_ver.split(".")
    lat_parts = lat_ver.split(".")
    split = next(
        (i for i, part in enumerate(lat_parts) if i >= len(cur_parts) or part != cur_parts[i]),
        len(lat_parts),
    )
    unchanged = ".".join(lat_parts[:split])
    changed = ".".join(lat_parts[split:])

    text = Text()
    if lat_op:
        text.append(lat_op, style="yellow" if lat_op != cur_op else "dim")
    if unchanged:
        text.append(unchanged)
        if changed:
            text.append(".")
    text.append(changed, style=f"bold {color}")
    return text


def _bump_counts_text(counts: dict[str, int], *, empty: tuple[str, str]) -> Text:
    """Join per-bump counts into a colored 'N major, N minor, N patch' summary."""
    text = Text()
    if not counts:
        text.append(empty[0], style=empty[1])
        return text
    first = True
    for bump in ("major", "minor", "patch"):
        if not counts.get(bump):
            continue
        if not first:
            text.append(", ", style="dim")
        text.append(str(counts[bump]), style=BUMP_COLOR.get(bump, "dim"))
        text.append(f" {bump}")
        first = False
    return text


def render_file_header(label: str, infos: list[DepInfo], mode: str) -> Text:
    """Build the top-level '{name} - N major, N minor' header, like upstream's renderChanges."""
    counts: dict[str, int] = {}
    for info in infos:
        if info.is_shown(mode):
            counts[info.bump] = counts.get(info.bump, 0) + 1
    header = Text()
    header.append(label, style="bold cyan")
    header.append(" - ", style="dim")
    header.append_text(_bump_counts_text(counts, empty=("up to date", "dim green")))
    return header


def _hint_line(text: str, keywords: list[str]) -> Text:
    """Render a dim control-hint line with its keywords highlighted, like upstream's FIG_BLOCK lines."""
    line = Text("┃ ", style="dim")
    parts = [rf"\b{re.escape(k)}\b" if k.isalpha() else re.escape(k) for k in keywords]
    pattern = re.compile("(" + "|".join(parts) + ")")
    pos = 0
    for match in pattern.finditer(text):
        if match.start() > pos:
            line.append(text[pos : match.start()], style="dim")
        line.append(match.group(), style="bold green")
        pos = match.end()
    if pos < len(text):
        line.append(text[pos:], style="dim")
    return line


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

    console.print(Text(f"  {label}", style="bold blue"))

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
    table.add_column("cur_age", no_wrap=True, min_width=cur_age_width, justify="right")
    table.add_column("current", style="dim", no_wrap=True, min_width=spec_width, justify="right")
    table.add_column("arrow", no_wrap=True)
    table.add_column("latest", no_wrap=True, min_width=latest_spec_width, justify="right")
    table.add_column("lat_age", no_wrap=True, min_width=lat_age_width, justify="right")

    for info in visible:
        color = BUMP_COLOR.get(info.bump, "dim")
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
            )
        elif info.bump == "same":
            table.add_row(
                Text(info.name, style="dim"),
                Text(cur_age, style="dim"),
                Text(info.current_spec, style="dim"),
                Text("·", style="dim"),
                Text(info.current_spec, style="dim"),
                "",
            )
        else:
            table.add_row(
                info.name,
                Text(cur_age, style=cur_age_color),
                Text(info.current_spec, style="dim"),
                Text("→", style="dim"),
                _colorize_diff(info.current_spec, info.latest_spec, color),
                Text(lat_age, style=lat_age_color),
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


def interactive_select(
    outdated: list[DepInfo],
    categories: list[tuple[str, list[tuple[str, list[DepInfo]]]]] | None = None,
) -> list[DepInfo]:
    """Run the full-screen selector in a TTY, with numeric input as CI fallback."""
    if not outdated:
        return []
    if not _interactive_tty():
        return _interactive_numbers(outdated)
    try:
        return _interactive_menu(outdated, categories)
    except ImportError, OSError, ValueError:
        return _interactive_numbers(outdated)


def _interactive_tty() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)() and getattr(console.file, "isatty", lambda: False)())


def _interactive_numbers(outdated: list[DepInfo]) -> list[DepInfo]:
    console.print()
    console.print("  [bold]Select packages to update:[/]")
    for idx, info in enumerate(outdated, 1):
        color = BUMP_COLOR.get(info.bump, "dim")
        line = Text.from_markup(f"  [dim]{idx:>2}.[/]  [bold]{info.name}[/]  [dim]{info.current_spec}[/] [dim]→[/] ")
        line.append_text(_colorize_diff(info.current_spec, info.latest_spec, color))
        console.print(line)
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


def _interactive_menu(
    outdated: list[DepInfo],
    categories: list[tuple[str, list[tuple[str, list[DepInfo]]]]] | None = None,
) -> list[DepInfo]:
    import termios
    import tty

    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    selected = set(range(len(outdated)))
    cursor = 0
    rendered = 0
    stream = console.file

    def draw() -> None:
        nonlocal rendered
        rendered = _draw_lines(_menu_lines(outdated, cursor, selected, categories), rendered)

    try:
        tty.setcbreak(fd)
        console.clear()
        stream.write("\x1b[?25l")
        stream.flush()
        draw()
        while True:
            key = _read_key(fd)
            if key in ("up", "k"):
                cursor = (cursor - 1) % len(outdated)
            elif key in ("down", "j"):
                cursor = (cursor + 1) % len(outdated)
            elif key == "space":
                if cursor in selected:
                    selected.remove(cursor)
                else:
                    selected.add(cursor)
            elif key == "all":
                selected = set() if len(selected) == len(outdated) else set(range(len(outdated)))
            elif key in ("right", "l"):
                if _interactive_version_select(outdated[cursor]):
                    rendered = 0
            elif key == "enter":
                return [info for index, info in enumerate(outdated) if index in selected]
            elif key in ("escape", "q", "ctrl-c"):
                return []
            else:
                continue
            draw()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original)
        console.clear()
        stream.write("\x1b[?25h")
        stream.flush()


def _interactive_version_select(info: DepInfo) -> bool:
    versions = list(dict.fromkeys(v for v in info.available_versions if v and v != info.current))
    if not versions:
        return False

    cursor = max(0, versions.index(info.latest)) if info.latest in versions else 0
    rendered = 0

    console.clear()
    try:
        while True:
            rendered = _draw_lines(_version_menu_lines(info, versions, cursor), rendered)
            key = _read_key(sys.stdin.fileno())
            if key in ("up", "k"):
                cursor = (cursor - 1) % len(versions)
            elif key in ("down", "j"):
                cursor = (cursor + 1) % len(versions)
            elif key in ("enter", "left", "right"):
                info.latest = versions[cursor]
                info.release_date = None
                info.bump = calc_bump(info.current, info.latest)
                info.effective_mode = "major"
                return True
            elif key in ("escape", "q", "ctrl-c"):
                return True
    finally:
        console.clear()


def _draw_lines(lines: list[Text], previous: int) -> int:
    stream = console.file
    if previous:
        stream.write(f"\x1b[{previous}A")
    for line in lines:
        stream.write("\x1b[2K\r")
        console.print(line, end="\n", no_wrap=True, overflow="crop")
    stream.flush()
    return len(lines)


def _read_key(fd: int) -> str | None:
    import os

    key = os.read(fd, 1)
    if not key:
        return "escape"
    if key in (b"\r", b"\n"):
        return "enter"
    if key == b" ":
        return "space"
    if key in (b"a", b"A"):
        return "all"
    if key in (b"q", b"Q"):
        return "q"
    if key in (b"k", b"K"):
        return "k"
    if key in (b"j", b"J"):
        return "j"
    if key in (b"h", b"H"):
        return "left"
    if key in (b"l", b"L"):
        return "right"
    if key == b"\x03":
        return "ctrl-c"
    if key != b"\x1b":
        return None

    if not select.select([fd], [], [], 0.05)[0]:
        return "escape"
    bracket = os.read(fd, 1)
    if bracket not in (b"[", b"O"):
        return "escape"
    code = os.read(fd, 1)
    return {b"A": "up", b"B": "down", b"C": "right", b"D": "left"}.get(code)


def _menu_lines(
    outdated: list[DepInfo],
    cursor: int,
    selected: set[int],
    categories: list[tuple[str, list[tuple[str, list[DepInfo]]]]] | None = None,
) -> list[Text]:
    fixed = [
        _hint_line("↑↓ to select, space to toggle, → to change version", ["↑↓", "space", "→"]),
        _hint_line("enter to confirm, esc to cancel, a to select/unselect all", ["enter", "esc", "a"]),
        Text(),
    ]
    body = _menu_body(outdated, cursor, selected, categories)
    height = shutil.get_terminal_size((120, 24)).lines
    limit = max(1, height - len(fixed) - 1)
    if len(body) > limit:
        focused = next((position for position, (_, index) in enumerate(body) if index == cursor), 0)
        start = max(0, min(focused - limit // 2, len(body) - limit))
        visible = body[start : start + limit]
        visible.append((Text("  -- END --", style="yellow"), None))
    else:
        visible = body
    return fixed + [line for line, _ in visible]


def _menu_body(
    outdated: list[DepInfo],
    cursor: int,
    selected: set[int],
    categories: list[tuple[str, list[tuple[str, list[DepInfo]]]]] | None,
) -> list[tuple[Text, int | None]]:
    widths = _menu_widths(outdated)
    body: list[tuple[Text, int | None]] = []
    index = 0
    files = categories or [("", [("", outdated)])]
    for file_label, groups in files:
        file_infos = [info for _, infos in groups for info in infos]
        if file_label:
            file_range = range(index, index + len(file_infos))
            counts: dict[str, int] = {}
            for info, position in zip(file_infos, file_range, strict=True):
                if position in selected:
                    counts[info.bump] = counts.get(info.bump, 0) + 1
            header = Text()
            header.append(file_label, style="bold cyan")
            header.append(" - ", style="dim")
            header.append_text(_bump_counts_text(counts, empty=("no change", "dim")))
            body.append((header, None))
            body.append((Text(), None))

        first_group = True
        for label, infos in groups:
            if label:
                if not first_group:
                    body.append((Text(), None))
                body.append((Text(f"  {label}", style="bold blue"), None))
            first_group = False
            for info in infos:
                body.append((_menu_dependency_line(info, index, cursor, selected, widths), index))
                index += 1

        if file_label:
            body.append((Text(), None))
    return body


def _menu_widths(infos: list[DepInfo]) -> tuple[int, int, int, int, int]:
    return (
        max((len(info.name) for info in infos), default=0),
        max((len(_age(info.current_release_date)[0]) for info in infos), default=0),
        max((len(info.current_spec) for info in infos), default=0),
        max((len(info.latest_spec) for info in infos), default=0),
        max((len(_age(info.release_date)[0]) for info in infos), default=0),
    )


def _append_cell(line: Text, value: str, width: int, style: str | None = None, *, align: str = "left") -> None:
    padded = value.rjust(width) if align == "right" else value.ljust(width)
    line.append(padded, style=style)


def _append_diff_cell(line: Text, current_spec: str, latest_spec: str, color: str, width: int, checked: bool) -> None:
    if not checked:
        _append_cell(line, latest_spec, width, "dim strike", align="right")
        return
    diff = _colorize_diff(current_spec, latest_spec, color)
    pad = width - len(diff.plain)
    if pad > 0:
        line.append(" " * pad)
    line.append_text(diff)


def _menu_dependency_line(
    info: DepInfo,
    index: int,
    cursor: int,
    selected: set[int],
    widths: tuple[int, int, int, int, int],
) -> Text:
    name_width, current_age_width, current_width, latest_width, latest_age_width = widths
    checked = index in selected
    color = BUMP_COLOR.get(info.bump, "dim")
    current_age, current_age_color = _age(info.current_release_date)
    latest_age, latest_age_color = _age(info.release_date)
    row_style = None if checked else "dim"
    line = Text("  ")
    line.append("❯ " if index == cursor else "  ", style="bold cyan" if index == cursor else "")
    line.append("◉ " if checked else "◌ ", style="bold green" if checked else "dim")
    _append_cell(line, info.name, name_width, "bold" if index == cursor and checked else row_style)
    line.append("  ")
    _append_cell(line, current_age, current_age_width, current_age_color if checked else "dim", align="right")
    line.append("  ")
    _append_cell(line, info.current_spec, current_width, "dim", align="right")
    line.append("  ")
    line.append("→" if checked else "·", style="dim")
    line.append("  ")
    _append_diff_cell(line, info.current_spec, info.latest_spec, color, latest_width, checked)
    line.append("  ")
    _append_cell(line, latest_age, latest_age_width, latest_age_color if checked else "dim", align="right")
    return line


def _version_menu_lines(info: DepInfo, versions: list[str], cursor: int) -> list[Text]:
    fixed = [
        Text(f"┃ Select a version for {info.name} (current {info.current_spec})", style="dim"),
        _hint_line("↑↓ to select, enter to confirm, esc to go back", ["↑↓", "enter", "esc"]),
        Text(),
    ]
    width = max((len(version) for version in versions), default=0)
    lines = fixed[:]
    for index, version in enumerate(versions):
        bump = calc_bump(info.current, version)
        color = BUMP_COLOR.get(bump, "dim")
        focused = index == cursor
        line = Text("❯ " if focused else "  ", style="bold cyan" if focused else "")
        _append_cell(line, version, width, "bold" if focused else "dim")
        line.append("  ")
        line.append(info.current_spec, style="dim")
        line.append("  →  ", style="dim")
        line.append(version, style=f"bold {color}" if focused else "dim")
        lines.append(line)
    return lines
