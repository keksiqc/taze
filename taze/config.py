"""Loading, validating, and merging project-local taze configuration."""

from __future__ import annotations

import fnmatch
import re
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import Field, ValidationError
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    PyprojectTomlConfigSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from taze.models import MODES


if TYPE_CHECKING:
    import typer


ConfigError = ValidationError


class TazeConfig(BaseSettings):
    """Every option that can be set from ``taze.toml`` / ``[tool.taze]``.

    Field names must match the corresponding ``main()`` parameter name exactly —
    that's what :func:`resolve_config` uses to look up whether the CLI flag was
    passed explicitly, so a mismatch would silently make the setting inert.
    """

    model_config = SettingsConfigDict(
        extra="ignore",
        frozen=True,
        strict=True,
    )

    mode: str = "default"
    include: str | None = None
    exclude: str | None = None
    recursive: bool = False
    ignore_paths: str | list[str] | None = None
    ignore_other_workspaces: bool = True
    interactive: bool = False
    all_deps: bool = False
    group: bool = True
    include_locked: bool = False
    maturity_period: int = 0
    maturity_period_exclude: str | None = None
    package_mode: dict[str, str] = Field(default_factory=dict)
    sort: str | None = "diff-asc"
    fail_on_outdated: bool = False
    silent: bool = False
    output_json: bool = False
    github_actions: bool = True
    github_actions_style: str = "auto"
    github_actions_pin: bool = False
    concurrency: int = 10
    request_timeout: float = 10.0
    retries: int = 2
    write: bool = False
    install: bool = False
    update: bool = False
    force: bool = False

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def load_config(root: Path, config_path: Path | None = None) -> dict[str, Any]:
    """Load ``taze.toml`` or ``[tool.taze]``, validated against :class:`TazeConfig`.

    Unknown keys are ignored (so older/newer taze versions can share a config
    file); recognized keys are validated by :class:`TazeConfig`.
    """
    toml_file = config_path or root / "taze.toml"
    pyproject_file = root / "pyproject.toml"
    try:
        toml_table_header = _toml_table_header(toml_file)
    except OSError, tomllib.TOMLDecodeError:
        return {}

    class ProjectConfig(TazeConfig):
        model_config = SettingsConfigDict(env_prefix="TAZE_", pyproject_toml_table_header=("tool", "taze"))

        @classmethod
        def settings_customise_sources(
            cls,
            settings_cls: type[BaseSettings],
            init_settings: PydanticBaseSettingsSource,
            env_settings: PydanticBaseSettingsSource,
            dotenv_settings: PydanticBaseSettingsSource,
            file_secret_settings: PydanticBaseSettingsSource,
        ) -> tuple[PydanticBaseSettingsSource, ...]:
            return (
                init_settings,
                env_settings,
                TomlConfigSettingsSource(settings_cls, toml_file, toml_table_header),
                PyprojectTomlConfigSettingsSource(settings_cls, pyproject_file),
            )

    try:
        return ProjectConfig().model_dump(exclude_unset=True)
    except OSError, tomllib.TOMLDecodeError:
        return {}


def _toml_table_header(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        return ()
    with path.open("rb") as file:
        data = tomllib.load(file)
    tool = data.get("tool")
    if isinstance(tool, dict) and isinstance(tool.get("taze"), dict):
        return ("tool", "taze")
    return ("taze",) if isinstance(data.get("taze"), dict) else ()


def resolve_config(context: typer.Context, cli: TazeConfig, overrides: dict[str, Any]) -> TazeConfig:
    """Layer project-file settings under explicit CLI flags: CLI flag > config file > CLI default."""
    changes: dict[str, Any] = {}
    for name, value in overrides.items():
        try:
            source = context.get_parameter_source(name)
        except Exception:  # noqa: BLE001 - defensive: never let config resolution crash on this
            source = None
        if source is None or source.name == "DEFAULT":
            changes[name] = value
    return cli.model_copy(update=changes) if changes else cli


def package_mode_for(name: str, package_modes: object) -> str | None:
    """Return an exact or slash-delimited-regex policy for a package name."""
    if not isinstance(package_modes, dict):
        return None
    for pattern, mode in package_modes.items():
        if not isinstance(pattern, str) or not isinstance(mode, str):
            continue
        if mode != "ignore" and mode not in MODES:
            continue
        normalized_pattern = pattern.lower().replace("_", "-")
        if normalized_pattern == name:
            return mode
        if pattern.startswith("/") and pattern.endswith("/"):
            try:
                if re.search(pattern[1:-1], name):
                    return mode
            except re.error:
                continue
        if fnmatch.fnmatchcase(name, pattern.lower().replace("_", "-")):
            return mode
    return None
