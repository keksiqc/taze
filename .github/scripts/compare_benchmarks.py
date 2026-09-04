#!/usr/bin/env python3
"""Render a Markdown table comparing two pytest-benchmark JSON reports."""

from __future__ import annotations

import json
import sys


def _load(path: str) -> dict[str, dict]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return {bench["fullname"]: bench["stats"] for bench in data.get("benchmarks", [])}


def _fmt(seconds: float) -> str:
    if seconds < 1e-3:
        return f"{seconds * 1e6:.2f} µs"
    if seconds < 1:
        return f"{seconds * 1e3:.2f} ms"
    return f"{seconds:.3f} s"


def main() -> None:
    base_path, pr_path = sys.argv[1], sys.argv[2]
    base = _load(base_path)
    pr = _load(pr_path)
    names = sorted(set(base) | set(pr))

    lines = [
        "### 📊 Version resolution benchmarks",
        "",
        "Comparing `tests/core/test_resolution_benchmarks.py` mean timings against the base branch.",
        "",
        "| Benchmark | Base (mean) | PR (mean) | Change |",
        "| --- | ---: | ---: | ---: |",
    ]

    if not names:
        lines.append("| _no benchmarks found_ | | | |")
    for name in names:
        short = name.rsplit("::", 1)[-1]
        base_mean = base.get(name, {}).get("mean")
        pr_mean = pr.get(name, {}).get("mean")
        if base_mean is None:
            lines.append(f"| {short} | — | {_fmt(pr_mean)} | new |")
            continue
        if pr_mean is None:
            lines.append(f"| {short} | {_fmt(base_mean)} | — | removed |")
            continue
        delta = (pr_mean - base_mean) / base_mean * 100
        arrow = "🟢" if delta <= -5 else "🔴" if delta >= 5 else "⚪"
        lines.append(f"| {short} | {_fmt(base_mean)} | {_fmt(pr_mean)} | {arrow} {delta:+.1f}% |")

    lines += ["", "_🟢 ≥5% faster · 🔴 ≥5% slower · ⚪ within noise_"]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
