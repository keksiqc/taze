from __future__ import annotations

from unittest.mock import patch

from taze.registries.github import fetch_github_action_info


def test_floating_major_tag_is_not_rewritten_in_default_mode() -> None:
    tags = [
        {"name": "v3", "commit": {"sha": "0" * 40}},
        {"name": "v3.6.0", "commit": {"sha": "1" * 40}},
    ]
    with patch("taze.registries.github._request_tags", return_value=tags):
        latest, _, _, _ = fetch_github_action_info("actions/checkout", current_version="v3", mode="default")
    assert latest == "v3"


def test_action_resolution_honours_mode() -> None:
    tags = [
        {"name": "v4", "commit": {"sha": "0" * 40}},
        {"name": "v4.1.0", "commit": {"sha": "1" * 40}},
        {"name": "v5", "commit": {"sha": "2" * 40}},
    ]
    with patch("taze.registries.github._request_tags", return_value=tags):
        latest, _, _, _ = fetch_github_action_info("actions/checkout", current_version="v4.0.0", mode="minor")
    assert latest == "v4.1.0"
