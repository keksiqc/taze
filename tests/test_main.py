from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from taze import __version__
from taze.main import app
from taze.registries.pypi import PypiResolution


def _invoke(args: list[str], *, latest: str | None = "2.1", input: str | None = None):
    """Run the CLI with every PyPI lookup answering ``latest`` ("current" echoes the pinned version)."""

    def lookup(name, *, current_version=None, **_):
        version = current_version if latest == "current" else latest
        return PypiResolution(version, None, None)

    with patch("taze.core.resolution.fetch_pypi_info", side_effect=lookup):
        return CliRunner().invoke(app, args, input=input)


@pytest.fixture
def project(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["requests>=2.0"]\n\n[dependency-groups]\ndev = ["httpx>=1.0"]\n'
    )
    return tmp_path


def test_cli_reads_requirements_file(tmp_path) -> None:
    path = tmp_path / "requirements.txt"
    path.write_text("# comment\nrequests>=2.0  # pinned\n")
    result = _invoke(["--cwd", str(tmp_path), "--json", "--silent"])
    assert result.exit_code == 0
    info = json.loads(result.stdout)[str(path)]["requirements"][0]
    assert (info["name"], info["current"], info["latest"]) == ("requests", "2.0", "2.1")


def test_cli_reads_taze_config(tmp_path) -> None:
    path = tmp_path / "requirements.txt"
    path.write_text("requests>=2\n")
    (tmp_path / "taze.toml").write_text("[tool.taze]\noutput_json = true\nsilent = true\n")
    result = _invoke(["--cwd", str(tmp_path)])
    assert result.exit_code == 0
    assert json.loads(result.stdout)[str(path)]["requirements"][0]["name"] == "requests"


def test_cli_json_output_stays_valid_when_a_file_fails_to_parse(tmp_path) -> None:
    (tmp_path / "requirements.txt").write_text("requests>=2\n")
    (tmp_path / "requirements-dev.txt").write_bytes(b"\xff\xfe not utf-8")
    result = _invoke(["--cwd", str(tmp_path), "--json"])
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


def test_cli_version() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"taze/{__version__}"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["bogus"], "Unknown mode"),
        (["--sort", "sideways"], "--sort must be one of"),
        (["--github-actions-style", "hash"], "--github-actions-style must be"),
        (["--concurrency", "0"], "must be positive"),
        (["-n", "/[/"], "Invalid dependency filter"),
    ],
)
def test_cli_rejects_bad_options(project, args, message) -> None:
    result = _invoke(["--cwd", str(project), *args])
    assert result.exit_code == 1
    assert message in result.stderr


def test_cli_fails_without_dependency_files(tmp_path) -> None:
    result = _invoke(["--cwd", str(tmp_path)])
    assert result.exit_code == 1
    assert "No supported dependency files" in result.stderr


class TestExitCodes:
    def test_outdated_exits_zero_by_default(self, project) -> None:
        assert _invoke(["--cwd", str(project)]).exit_code == 0

    def test_fail_on_outdated_exits_one_when_outdated(self, project) -> None:
        result = _invoke(["--cwd", str(project), "--fail-on-outdated"])
        assert result.exit_code == 1
        assert "requests" in result.stdout

    def test_check_alias_and_silent(self, project) -> None:
        result = _invoke(["--cwd", str(project), "--check", "--silent"])
        assert result.exit_code == 1
        assert result.stdout == ""

    def test_fail_on_outdated_exits_zero_when_current(self, project) -> None:
        result = _invoke(["--cwd", str(project), "--check"], latest="current")
        assert result.exit_code == 0
        assert "already up-to-date" in result.stdout

    def test_fail_on_outdated_applies_to_json(self, project) -> None:
        assert _invoke(["--cwd", str(project), "--json", "--check"]).exit_code == 1
        assert _invoke(["--cwd", str(project), "--json", "--check"], latest="current").exit_code == 0


class TestWrite:
    def test_dry_run_leaves_file_alone_and_hints(self, project) -> None:
        before = (project / "pyproject.toml").read_text()
        result = _invoke(["--cwd", str(project)])
        assert (project / "pyproject.toml").read_text() == before
        assert "taze -w" in result.stdout

    def test_write_updates_pyproject_in_place(self, project) -> None:
        result = _invoke(["--cwd", str(project), "-w"])
        assert result.exit_code == 0
        content = (project / "pyproject.toml").read_text()
        assert 'dependencies = ["requests>=2.1"]' in content
        assert 'dev = ["httpx>=2.1"]' in content
        assert "Wrote 2 update(s)" in result.stdout
        assert "uv sync" in result.stdout

    def test_write_respects_mode(self, project) -> None:
        _invoke(["--cwd", str(project), "patch", "-w"])
        content = (project / "pyproject.toml").read_text()
        assert "requests>=2.0" in content  # 2.0 → 2.1 is a minor bump, not allowed in patch mode
        assert "httpx>=1.0" in content

    def test_json_mode_can_write(self, project) -> None:
        result = _invoke(["--cwd", str(project), "--json", "-w"])
        assert json.loads(result.stdout)
        assert "requests>=2.1" in (project / "pyproject.toml").read_text()

    def test_install_runs_installer_after_writing(self, project) -> None:
        completed = type("Completed", (), {"returncode": 0})()
        with patch("taze.core.runner.subprocess.run", return_value=completed) as run:
            result = _invoke(["--cwd", str(project), "-i"])
        assert result.exit_code == 0
        assert run.call_args.args[0] == ["uv", "sync"]
        assert "requests>=2.1" in (project / "pyproject.toml").read_text()

    def test_install_failure_propagates_exit_code(self, project) -> None:
        completed = type("Completed", (), {"returncode": 3})()
        with patch("taze.core.runner.subprocess.run", return_value=completed):
            result = _invoke(["--cwd", str(project), "-i"])
        assert result.exit_code == 3
        assert "failed" in result.stderr


class TestInteractive:
    def test_numeric_fallback_writes_only_selection(self, project) -> None:
        # CliRunner is not a TTY, so the selector falls back to numbered input.
        result = _invoke(["--cwd", str(project), "-I"], input="1\ny\nn\n")
        assert result.exit_code == 0, result.output
        content = (project / "pyproject.toml").read_text()
        assert "requests>=2.1" in content
        assert "httpx>=1.0" in content
        assert "Wrote 1 update(s)" in result.stdout

    def test_empty_selection_exits_cleanly(self, project) -> None:
        result = _invoke(["--cwd", str(project), "-I"], input="\n")
        assert result.exit_code == 0
        assert "requests>=2.0" in (project / "pyproject.toml").read_text()


def test_cli_hides_up_to_date_entries_unless_all(project) -> None:
    with patch(
        "taze.core.resolution.fetch_pypi_info",
        side_effect=lambda name, **_: PypiResolution("2.1" if name == "requests" else "1.0", None, None),
    ):
        default = CliRunner().invoke(app, ["--cwd", str(project)])
        everything = CliRunner().invoke(app, ["--cwd", str(project), "-a"])
    assert "httpx" not in default.stdout
    assert "httpx" in everything.stdout
