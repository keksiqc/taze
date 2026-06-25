from __future__ import annotations

import textwrap
from pathlib import Path

from taze.models import DepInfo, FileKind
from taze.writers import write_pyproject_updates, write_requirements_updates


def _dep(raw: str, name: str, current: str, latest: str, operator: str = ">=", bump: str = "minor") -> DepInfo:
    return DepInfo(
        raw=raw,
        name=name,
        current=current,
        operator=operator,
        latest=latest,
        bump=bump,
        file_kind=FileKind.PYPROJECT,
    )


class TestWritePyprojectUpdates:
    def _write(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "pyproject.toml"
        p.write_text(textwrap.dedent(content))
        return p

    def test_updates_gte_constraint(self, tmp_path):
        p = self._write(
            tmp_path,
            """
            [project]
            dependencies = ["requests>=2.0.0"]
            """,
        )
        dep = _dep("requests>=2.0.0", "requests", "2.0.0", "3.0.0", ">=", "major")
        count = write_pyproject_updates(p, {"dependencies": [dep]})
        assert count == 1
        assert "requests>=3.0.0" in p.read_text()

    def test_skips_up_to_date(self, tmp_path):
        p = self._write(
            tmp_path,
            """
            [project]
            dependencies = ["requests>=2.0.0"]
            """,
        )
        dep = DepInfo(
            raw="requests>=2.0.0",
            name="requests",
            current="2.0.0",
            operator=">=",
            latest="2.0.0",
            bump="same",
        )
        count = write_pyproject_updates(p, {"dependencies": [dep]})
        assert count == 0
        assert "requests>=2.0.0" in p.read_text()

    def test_handles_single_quotes(self, tmp_path):
        p = self._write(
            tmp_path,
            "[project]\ndependencies = ['requests>=2.0.0']\n",
        )
        dep = _dep("requests>=2.0.0", "requests", "2.0.0", "3.0.0", ">=", "major")
        count = write_pyproject_updates(p, {"dependencies": [dep]})
        assert count == 1
        assert "requests>=3.0.0" in p.read_text()

    def test_multiple_deps(self, tmp_path):
        p = self._write(
            tmp_path,
            """
            [project]
            dependencies = ["requests>=2.0.0", "httpx>=0.20.0"]
            """,
        )
        deps = [
            _dep("requests>=2.0.0", "requests", "2.0.0", "3.0.0", ">=", "major"),
            _dep("httpx>=0.20.0", "httpx", "0.20.0", "0.27.0", ">=", "minor"),
        ]
        count = write_pyproject_updates(p, {"dependencies": deps})
        assert count == 2
        text = p.read_text()
        assert "requests>=3.0.0" in text
        assert "httpx>=0.27.0" in text


class TestWriteRequirementsUpdates:
    def _write(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "requirements.txt"
        p.write_text(textwrap.dedent(content))
        return p

    def _req_dep(self, raw: str, name: str, current: str, latest: str, lineno: int, operator: str = ">=") -> DepInfo:
        d = _dep(raw, name, current, latest, operator)
        d.line_number = lineno
        d.file_kind = FileKind.REQUIREMENTS
        return d

    def test_updates_line(self, tmp_path):
        p = self._write(tmp_path, "requests>=2.0.0\n")
        dep = self._req_dep("requests>=2.0.0", "requests", "2.0.0", "3.0.0", 1, ">=")
        dep.bump = "major"
        count = write_requirements_updates(p, [dep])
        assert count == 1
        assert p.read_text().strip() == "requests>=3.0.0"

    def test_preserves_inline_comment(self, tmp_path):
        p = self._write(tmp_path, "requests>=2.0.0  # pinned\n")
        dep = self._req_dep("requests>=2.0.0", "requests", "2.0.0", "3.0.0", 1, ">=")
        dep.bump = "major"
        write_requirements_updates(p, [dep])
        text = p.read_text()
        assert "requests>=3.0.0" in text
        assert "# pinned" in text

    def test_preserves_other_lines(self, tmp_path):
        p = self._write(tmp_path, "requests>=2.0.0\nhttpx>=0.20\n")
        dep = self._req_dep("requests>=2.0.0", "requests", "2.0.0", "3.0.0", 1, ">=")
        dep.bump = "major"
        write_requirements_updates(p, [dep])
        lines = p.read_text().splitlines()
        assert lines[0].startswith("requests>=3.0.0")
        assert lines[1] == "httpx>=0.20"

    def test_skips_up_to_date(self, tmp_path):
        p = self._write(tmp_path, "requests>=2.0.0\n")
        dep = self._req_dep("requests>=2.0.0", "requests", "2.0.0", "2.0.0", 1, ">=")
        dep.bump = "same"
        count = write_requirements_updates(p, [dep])
        assert count == 0
