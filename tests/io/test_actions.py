from __future__ import annotations

from pathlib import Path

from taze.io.actions import parse_actions, write_action_updates


def test_parse_and_write_action_refs(tmp_path: Path) -> None:
    sha = "0" * 40
    path = tmp_path / "action.yml"
    path.write_text(f"steps:\n  - uses: actions/checkout@v4\n  - uses: actions/setup-python@{sha} # v4.1.0\n")

    infos = parse_actions(path)
    assert [(info.name, info.current, info.action_style) for info in infos] == [
        ("actions/checkout", "v4", "tag"),
        ("actions/setup-python", "v4.1.0", "sha"),
    ]

    infos[0].latest, infos[0].bump = "v5.0.0", "major"
    infos[1].latest, infos[1].bump = "v5.0.0", "major"
    infos[1].action_target_sha = "1" * 40
    assert write_action_updates(path, infos) == 2
    text = path.read_text()
    assert "actions/checkout@v5" in text
    assert f"actions/setup-python@{'1' * 40} # v5.0.0" in text
