from __future__ import annotations

import json
from unittest.mock import patch

from typer.testing import CliRunner

from taze.main import app, resolve_deps


def test_resolve_deps_skips_local_workspace_packages() -> None:
    with patch("taze.main.fetch_pypi_info") as fetch:
        resolved = resolve_deps(
            [("shared-lib>=1.0", None)],
            include_pat=None,
            exclude_pat=None,
            pre=False,
            mode="default",
            include_locked=False,
            maturity_period=0,
            maturity_exclude_pat=None,
            package_modes={},
            local_package_names={"shared-lib"},
            concurrency=1,
        )
    assert resolved == []
    fetch.assert_not_called()


def test_cli_reads_requirements_file(tmp_path) -> None:
    path = tmp_path / "requirements.txt"
    path.write_text("# comment\nrequests>=2.0  # pinned\n")
    with patch("taze.main.fetch_pypi_info", return_value=("2.1", None, None)):
        result = CliRunner().invoke(app, ["--cwd", str(tmp_path), "--json", "--silent"])
    assert result.exit_code == 0
    info = json.loads(result.stdout)[str(path)]["requirements"][0]
    assert (info["name"], info["current"], info["latest"]) == ("requests", "2.0", "2.1")
