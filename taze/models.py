from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version, InvalidVersion

BUMP_ORDER: dict[str, int] = {"major": 3, "minor": 2, "patch": 1, "same": 0, "?": -1}

BUMP_COLOR: dict[str, str] = {
    "major": "red",
    "minor": "yellow",
    "patch": "green",
    "same": "dim",
    "?": "dim",
}
BUMP_BADGE: dict[str, str] = {
    "major": "[bold red]MAJOR[/]",
    "minor": "[yellow]minor[/]",
    "patch": "[green]patch[/]",
    "same": "[dim]up to date[/]",
    "?": "[dim]?[/]",
}

# Mode → (min_bump_level, include_pre)
# min_bump_level: only show updates at this level or above
MODE_SETTINGS: dict[str, tuple[str, bool]] = {
    "default": ("patch", False),
    "major": ("patch", False),
    "latest": ("patch", False),
    "stable": ("patch", False),
    "minor": ("patch", False),  # filter applied post-fetch
    "patch": ("patch", False),  # filter applied post-fetch
    "newest": ("patch", True),
    "next": ("patch", True),
}

MODES = list(MODE_SETTINGS)

# Which bump levels each mode actually shows
MODE_MIN_BUMP: dict[str, str] = {
    "default": "patch",
    "major": "patch",
    "latest": "patch",
    "stable": "patch",
    "newest": "patch",
    "next": "patch",
    "minor": "patch",  # no major bumps
    "patch": "patch",  # no minor or major bumps
}

MODE_SHOWS_MAJOR: dict[str, bool] = {
    "major": True,
    "default": True,
    "latest": True,
    "stable": True,
    "newest": True,
    "next": True,
    "minor": False,
    "patch": False,
}
MODE_SHOWS_MINOR: dict[str, bool] = {
    "major": True,
    "default": True,
    "latest": True,
    "stable": True,
    "newest": True,
    "next": True,
    "minor": True,
    "patch": False,
}


class FileKind(str, Enum):
    PYPROJECT = "pyproject"
    REQUIREMENTS = "requirements"


def calc_bump(current: str | None, latest: str | None) -> str:
    if not current or not latest:
        return "?"
    try:
        c = Version(current)
        la = Version(latest)
        if la <= c:
            return "same"
        if la.major > c.major:
            return "major"
        if la.minor > c.minor:
            return "minor"
        return "patch"
    except InvalidVersion:
        return "?"


def bump_allowed(bump: str, mode: str) -> bool:
    """Return True if this bump level should be shown/updated in the given mode."""
    if bump in ("same", "?"):
        return False
    if bump == "major" and not MODE_SHOWS_MAJOR.get(mode, True):
        return False
    if bump == "minor" and not MODE_SHOWS_MINOR.get(mode, True):
        return False
    return True


@dataclass
class DepInfo:
    raw: str
    name: str
    current: str | None
    operator: str | None
    source_file: Path | None = None
    file_kind: FileKind = FileKind.PYPROJECT
    line_number: int | None = None
    latest: str | None = None
    release_date: str | None = None  # ISO date of latest release
    current_release_date: str | None = None  # ISO date of the current pinned release
    bump: str = "?"
    fetch_error: bool = False

    @property
    def current_spec(self) -> str:
        if self.operator and self.current:
            return f"{self.operator}{self.current}"
        return "(any)"

    @property
    def latest_spec(self) -> str:
        if not self.latest:
            return "—"
        if self.operator:
            if self.operator == "~=":
                n = len(self.current.split(".")) if self.current else 2
                parts = self.latest.split(".")[:n]
                return f"~={'.'.join(parts)}"
            return f"{self.operator}{self.latest}"
        return self.latest

    @property
    def is_outdated(self) -> bool:
        return self.bump not in ("same", "?") and not self.fetch_error

    def is_shown(self, mode: str) -> bool:
        """True if this dep's update should be shown in the given mode."""
        if self.fetch_error:
            return True
        if not self.is_outdated:
            return False
        return bump_allowed(self.bump, mode)

    def updated_raw(self) -> str:
        if not self.latest or not self.operator:
            return self.raw
        try:
            req = Requirement(self.raw)
        except Exception:
            return self.raw

        new_specs: list[str] = []
        for spec in req.specifier:
            if spec.operator in (">=", "==", ">"):
                new_specs.append(f"{spec.operator}{self.latest}")
            elif spec.operator == "~=":
                n = len(spec.version.split("."))
                parts = self.latest.split(".")[:n]
                new_specs.append(f"~={'.'.join(parts)}")
            else:
                new_specs.append(str(spec))

        extras = f"[{','.join(sorted(req.extras))}]" if req.extras else ""
        return f"{req.name}{extras}{','.join(new_specs)}"
