from __future__ import annotations

import json
from unittest.mock import patch

from typer.testing import CliRunner

from taze.main import app


def test_cli_reads_requirements_file(tmp_path) -> None:
    path = tmp_path / "requirements.txt"
    path.write_text("# comment\nrequests>=2.0  # pinned\n")
    with patch("taze.core.resolution.fetch_pypi_info", return_value=("2.1", None, None)):
        result = CliRunner().invoke(app, ["--cwd", str(tmp_path), "--json", "--silent"])
    assert result.exit_code == 0
    info = json.loads(result.stdout)[str(path)]["requirements"][0]
    assert (info["name"], info["current"], info["latest"]) == ("requests", "2.0", "2.1")


def test_cli_reads_taze_config(tmp_path) -> None:
    path = tmp_path / "requirements.txt"
    path.write_text("requests>=2\n")
    (tmp_path / "taze.toml").write_text("[tool.taze]\noutput_json = true\nsilent = true\n")
    with patch("taze.core.resolution.fetch_pypi_info", return_value=("2.1", None, None)):
        result = CliRunner().invoke(app, ["--cwd", str(tmp_path)])
    assert result.exit_code == 0
    assert json.loads(result.stdout)[str(path)]["requirements"][0]["name"] == "requests"


def test_cli_json_output_stays_valid_when_a_file_fails_to_parse(tmp_path) -> None:
    (tmp_path / "requirements.txt").write_text("requests>=2\n")
    (tmp_path / "requirements-dev.txt").write_bytes(b"\xff\xfe not utf-8")
    with patch("taze.core.resolution.fetch_pypi_info", return_value=("2.1", None, None)):
        result = CliRunner().invoke(app, ["--cwd", str(tmp_path), "--json"])
    assert result.exit_code == 0
    assert "Failed to parse" in result.stderr
    assert "Failed to parse" not in result.stdout
    assert json.loads(result.stdout)


def test_cli_reports_invalid_configuration_on_stderr(tmp_path) -> None:
    (tmp_path / "requirements.txt").write_text("requests>=2\n")
    (tmp_path / "taze.toml").write_text('concurrency = "many"\n')
    result = CliRunner().invoke(app, ["--cwd", str(tmp_path)])
    assert result.exit_code == 1
    assert "Invalid configuration" in result.stderr
