from __future__ import annotations

import re
from dataclasses import dataclass

from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import InvalidVersion, Version


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

MODES = ("default", "major", "latest", "stable", "minor", "patch", "newest", "next")
PRE_RELEASE_MODES = {"newest", "next"}

MODE_CEILING = {"minor": 2, "patch": 1}


def calc_bump(current: str | None, latest: str | None) -> str:
    """Return the bump level between current and latest version strings."""
    if not current or not latest:
        return "?"
    try:
        c = Version(current)
        la = Version(latest)
    except InvalidVersion:
        return "?"
    if la <= c:
        return "same"
    if la.major > c.major:
        return "major"
    if la.minor > c.minor:
        return "minor"
    return "patch"


def bump_allowed(bump: str, mode: str) -> bool:
    """Return True if this bump level should be shown/updated in the given mode."""
    return 0 < BUMP_ORDER.get(bump, -1) <= MODE_CEILING.get(mode, 3)


@dataclass
class DepInfo:
    """Holds parsed and resolved metadata for a single dependency."""

    raw: str
    name: str
    current: str | None
    operator: str | None
    line_number: int | None = None
    latest: str | None = None
    release_date: str | None = None  # ISO date of latest release
    current_release_date: str | None = None  # ISO date of the current pinned release
    bump: str = "?"
    fetch_error: bool = False
    effective_mode: str | None = None

    @property
    def current_spec(self) -> str:
        """Return the current version specifier string, e.g. '>=1.2.3'."""
        if self.operator and self.current:
            return f"{self.operator}{self.current}"
        return "(any)"

    @property
    def latest_spec(self) -> str:
        """Return the latest version as a specifier string, e.g. '>=2.0.0'."""
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
        """Return True if a newer version is available and was fetched successfully."""
        return self.bump not in ("same", "?") and not self.fetch_error

    @property
    def is_locked(self) -> bool:
        """Whether this dependency is an exact pin rather than a version range."""
        return self.operator in ("==", "===")

    def is_shown(self, mode: str) -> bool:
        """True if this dep's update should be shown in the given mode."""
        if self.fetch_error:
            return True
        if not self.is_outdated:
            return False
        return bump_allowed(self.bump, self.effective_mode or mode)

    def updated_raw(self) -> str:
        """Return the raw dependency string rewritten to pin the latest version."""
        if not self.latest or not self.operator:
            return self.raw
        try:
            Requirement(self.raw)
        except InvalidRequirement:
            return self.raw

        if not self.current:
            return self.raw
        version = self.latest
        if self.operator == "~=":
            parts = self.latest.split(".")[: len(self.current.split("."))]
            version = ".".join(parts)

        # Rewrite precisely the specifier we selected as the baseline. This
        # preserves markers, extras, upper bounds and comments in PEP 508
        # declarations instead of reserialising only the parsed name/specs.
        old = f"{self.operator}{self.current}"
        pattern = re.compile(rf"{re.escape(old)}(?![A-Za-z0-9_.!-])")
        return pattern.sub(f"{self.operator}{version}", self.raw, count=1)
