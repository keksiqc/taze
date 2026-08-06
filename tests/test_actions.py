from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from taze.actions import fetch_github_action_info, parse_actions, write_action_updates


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


def test_floating_major_tag_is_not_rewritten_in_default_mode() -> None:
    tags = [
        {"name": "v3", "commit": {"sha": "0" * 40}},
        {"name": "v3.6.0", "commit": {"sha": "1" * 40}},
    ]
    with patch("taze.actions._request_tags", return_value=tags):
        latest, _, _, _ = fetch_github_action_info("actions/checkout", current_version="v3", mode="default")
    assert latest == "v3"


def test_action_resolution_honours_mode() -> None:
    tags = [
        {"name": "v4", "commit": {"sha": "0" * 40}},
        {"name": "v4.1.0", "commit": {"sha": "1" * 40}},
        {"name": "v5", "commit": {"sha": "2" * 40}},
    ]
    with patch("taze.actions._request_tags", return_value=tags):
        latest, _, _, _ = fetch_github_action_info("actions/checkout", current_version="v4.0.0", mode="minor")
    assert latest == "v4.1.0"
