from __future__ import annotations

import json
from datetime import UTC, datetime

from rich.console import Console
from rich.text import Text

from taze import display
from taze.models import DepInfo


def test_age_returns_text_and_color() -> None:
    today = datetime.now(tz=UTC).date().isoformat()
    assert display._age(today) == ("~0d", "green")


def test_render_json_prints_machine_readable_output(monkeypatch) -> None:
    console = Console(record=True, force_terminal=False, color_system=None)
    monkeypatch.setattr(display, "console", console)
    info = DepInfo(
        raw="requests>=2.0",
        name="requests",
        current="2.0",
        operator=">=",
        latest="2.1",
        bump="minor",
    )
    display.render_json({"pyproject.toml": {"dependencies": [info]}})
    output = json.loads(console.export_text())
    assert output["pyproject.toml"]["dependencies"][0]["latest"] == "2.1"


def test_sort_infos_by_bump() -> None:
    infos = [
        DepInfo("a>=1", "a", "1", ">=", bump="patch"),
        DepInfo("b>=1", "b", "1", ">=", bump="major"),
    ]
    display._sort_infos(infos, "diff-desc")
    assert [info.name for info in infos] == ["b", "a"]


def test_interactive_select_accepts_numbers_and_ranges(monkeypatch) -> None:
    infos = [DepInfo(str(i), str(i), None, None) for i in range(1, 5)]
    monkeypatch.setattr("builtins.input", lambda: "1,3-4")
    assert display.interactive_select(infos) == [infos[0], infos[2], infos[3]]


def test_interactive_menu_renders_checked_cursor() -> None:
    info = DepInfo("requests>=2", "requests", "2", ">=", latest="3", bump="major")
    lines = display._menu_lines([info], 0, {0})
    plain = "\n".join(line.plain for line in lines if isinstance(line, Text))
    assert "☑" in plain
    assert "❯" in plain
