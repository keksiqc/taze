from __future__ import annotations

from taze.installers import install_command


class TestInstallCommand:
    def test_prefers_uv_lock(self, tmp_path) -> None:
        (tmp_path / "uv.lock").touch()
        assert install_command(tmp_path) == ["uv", "sync"]

    def test_detects_poetry(self, tmp_path) -> None:
        (tmp_path / "pyproject.toml").write_text("[tool.poetry]\nname = 'demo'\n")
        assert install_command(tmp_path) == ["poetry", "install"]

    def test_detects_pdm(self, tmp_path) -> None:
        (tmp_path / "pdm.lock").touch()
        assert install_command(tmp_path) == ["pdm", "install"]

    def test_detects_pixi(self, tmp_path) -> None:
        (tmp_path / "pixi.toml").touch()
        assert install_command(tmp_path) == ["pixi", "install"]
