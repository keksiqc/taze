from __future__ import annotations

from taze.discovery import discover_files


def _project(path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "pyproject.toml").write_text("[project]\nname = 'demo'\n")


class TestDiscoverFiles:
    def test_finds_root_dependency_files(self, tmp_path) -> None:
        _project(tmp_path)
        (tmp_path / "requirements-dev.txt").write_text("pytest\n")
        found = discover_files(tmp_path)
        assert [p.name for p in found] == ["pyproject.toml", "requirements-dev.txt"]

    def test_skips_default_ignored_directories(self, tmp_path) -> None:
        _project(tmp_path)
        _project(tmp_path / ".venv" / "lib")
        _project(tmp_path / "packages" / "api")
        found = discover_files(tmp_path, recursive=True)
        assert {p.parent.name for p in found} == {tmp_path.name, "api"}

    def test_honours_ignore_globs(self, tmp_path) -> None:
        _project(tmp_path)
        _project(tmp_path / "packages" / "api")
        _project(tmp_path / "examples" / "demo")
        found = discover_files(tmp_path, recursive=True, ignore_paths=("examples/**",))
        assert {p.parent.name for p in found} == {tmp_path.name, "api"}

    def test_skips_nested_workspace_by_default(self, tmp_path) -> None:
        _project(tmp_path)
        nested = tmp_path / "vendor" / "other"
        _project(nested)
        (nested / ".git").mkdir()
        found = discover_files(tmp_path, recursive=True)
        assert [p.parent for p in found] == [tmp_path]

    def test_can_include_nested_workspace(self, tmp_path) -> None:
        _project(tmp_path)
        nested = tmp_path / "vendor" / "other"
        _project(nested)
        (nested / ".git").mkdir()
        found = discover_files(tmp_path, recursive=True, ignore_other_workspaces=False)
        assert {p.parent for p in found} == {tmp_path, nested}
