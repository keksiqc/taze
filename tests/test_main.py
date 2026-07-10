from __future__ import annotations

from unittest.mock import patch

from taze.main import resolve_deps
from taze.models import FileKind


def test_resolve_deps_skips_local_workspace_packages() -> None:
    with patch("taze.main.fetch_pypi_info") as fetch:
        resolved = resolve_deps(
            [("shared-lib>=1.0", None, FileKind.PYPROJECT, None)],
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
