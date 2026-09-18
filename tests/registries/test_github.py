from __future__ import annotations

from unittest.mock import patch

from taze.registries import github
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


def test_gh_cli_token_is_looked_up_once(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    github._gh_cli_token.cache_clear()
    runs = []
    monkeypatch.setattr(github.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(
        github.subprocess, "run", lambda *a, **k: runs.append(a) or type("R", (), {"stdout": "tok\n"})()
    )
    assert github._github_token() == "tok"
    assert github._github_token() == "tok"
    assert len(runs) == 1
    github._gh_cli_token.cache_clear()


def test_rate_limit_is_final_and_flagged(monkeypatch) -> None:
    import io
    from email.message import Message
    from urllib.error import HTTPError

    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setattr(github, "rate_limit_hit", False)
    calls = []

    def limited(request, timeout=None):
        calls.append(request)
        headers = Message()
        headers["X-RateLimit-Remaining"] = "0"
        raise HTTPError(request.full_url, 403, "Forbidden", headers, io.BytesIO(b""))

    with patch("urllib.request.urlopen", side_effect=limited), patch("time.sleep") as mock_sleep:
        result = fetch_github_action_info("actions/checkout", current_version="v4", retries=2)

    assert result == (None, None, None, None)
    assert len(calls) == 1
    mock_sleep.assert_not_called()
    assert github.rate_limit_hit is True
    monkeypatch.setattr(github, "rate_limit_hit", False)
