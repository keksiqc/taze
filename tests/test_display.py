from __future__ import annotations

import json

from rich.console import Console

from taze import display
from taze.models import DepInfo


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
