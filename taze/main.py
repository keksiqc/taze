"""Typer command definition and configuration wiring."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from taze import __version__
from taze.config import ConfigError, TazeConfig, load_config, resolve_config
from taze.core.runner import run
from taze.ui.display import console


app = typer.Typer(
    name="taze",
    help="🥬 Keep your Python deps fresh",
    add_completion=False,
    rich_markup_mode="rich",
    no_args_is_help=False,
)


@app.command()
def main(
    context: typer.Context,
    mode: Annotated[
        str,
        typer.Argument(
            help=("Update mode: [dim]default | major | minor | patch | latest | newest | stable | next[/]"),
            show_default=False,
        ),
    ] = "default",
    cwd: Annotated[
        Path | None,
        typer.Option("--cwd", "-C", help="Working directory", show_default=False),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to a taze.toml configuration file"),
    ] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Bypass the local metadata cache")] = False,
    write: Annotated[bool, typer.Option("--write", "-w", help="Write updates back to file")] = False,
    install: Annotated[
        bool,
        typer.Option(
            "--install",
            "-i",
            help="Install directly after bumping (implies [cyan]-w[/])",
        ),
    ] = False,
    update: Annotated[
        bool,
        typer.Option("--update", "-u", help="Alias for [cyan]--install[/]"),
    ] = False,
    recursive: Annotated[
        bool,
        typer.Option(
            "--recursive",
            "-r",
            help="Recursively search for pyproject.toml / requirements*.txt",
        ),
    ] = False,
    ignore_paths: Annotated[
        str | None,
        typer.Option("--ignore-paths", help="Comma-separated glob paths to skip during recursive scans"),
    ] = None,
    ignore_other_workspaces: Annotated[
        bool,
        typer.Option(
            "--ignore-other-workspaces/--include-other-workspaces",
            help="Skip nested repositories and workspaces when scanning recursively",
        ),
    ] = True,
    interactive: Annotated[
        bool,
        typer.Option(
            "--interactive",
            "-I",
            help="Interactive mode — choose which packages to update",
        ),
    ] = False,
    include: Annotated[
        str | None,
        typer.Option(
            "--include",
            "-n",
            help="Only check these deps (comma-separated names or [dim]/regex/[/])",
        ),
    ] = None,
    exclude: Annotated[
        str | None,
        typer.Option(
            "--exclude",
            "-x",
            help="Skip these deps (comma-separated names or [dim]/regex/[/])",
        ),
    ] = None,
    all_deps: Annotated[bool, typer.Option("--all", "-a", help="Show up-to-date packages too")] = False,
    group: Annotated[
        bool,
        typer.Option("--group/--no-group", help="Group dependencies by source file on display"),
    ] = True,
    include_locked: Annotated[
        bool,
        typer.Option("--include-locked", "-l", help="Include exact (==) version pins"),
    ] = False,
    maturity_period: Annotated[
        int,
        typer.Option("--maturity-period", help="Wait this many days before accepting a new release"),
    ] = 0,
    maturity_period_exclude: Annotated[
        str | None,
        typer.Option("--maturity-period-exclude", help="Packages exempt from the maturity policy"),
    ] = None,
    sort: Annotated[
        str | None,
        typer.Option("--sort", help="Sort by: name-asc | name-desc | diff-asc | diff-desc"),
    ] = "diff-asc",
    fail_on_outdated: Annotated[
        bool,
        typer.Option(
            "--fail-on-outdated",
            "--check",
            help="Exit with code 1 if outdated dependencies are found",
        ),
    ] = False,
    silent: Annotated[bool, typer.Option("--silent", "-s", help="No output")] = False,
    output_json: Annotated[bool, typer.Option("--json", help="Machine-readable JSON output")] = False,
    github_actions: Annotated[
        bool,
        typer.Option(
            "--github-actions/--no-github-actions",
            help="Check versioned GitHub Actions references",
        ),
    ] = True,
    github_actions_style: Annotated[
        str,
        typer.Option("--github-actions-style", help="Action write style: auto | tag | sha"),
    ] = "auto",
    github_actions_pin: Annotated[
        bool,
        typer.Option(
            "--github-actions-pin",
            help="Pin GitHub Actions to their commit SHA even if already up to date (implies sha style)",
        ),
    ] = False,
    version: Annotated[
        bool,
        typer.Option("--version", "-v", help="Show version and exit", is_eager=True),
    ] = False,
    concurrency: Annotated[
        int,
        typer.Option("--concurrency", help="Number of concurrent registry requests"),
    ] = 10,
    request_timeout: Annotated[
        float,
        typer.Option("--request-timeout", help="Registry request timeout in seconds"),
    ] = 10.0,
    retries: Annotated[
        int,
        typer.Option("--retry", help="Retries after a failed registry request"),
    ] = 2,
    no_retry: Annotated[bool, typer.Option("--no-retry", help="Disable registry retries")] = False,
) -> None:
    if version:
        console.print(f"taze/{__version__}")
        raise typer.Exit(0)

    root = (cwd or Path()).resolve()
    config_path = (root / config).resolve() if config and not config.is_absolute() else config
    try:
        project_config = load_config(root, config_path)
    except ConfigError as error:
        console.print(f"[red]✗[/]  Invalid configuration: {error}")
        raise typer.Exit(1) from error

    cli_config = TazeConfig(
        mode=mode,
        include=include,
        exclude=exclude,
        recursive=recursive,
        ignore_paths=ignore_paths,
        ignore_other_workspaces=ignore_other_workspaces,
        interactive=interactive,
        all_deps=all_deps,
        group=group,
        include_locked=include_locked,
        maturity_period=maturity_period,
        maturity_period_exclude=maturity_period_exclude,
        sort=sort,
        fail_on_outdated=fail_on_outdated,
        silent=silent,
        output_json=output_json,
        github_actions=github_actions,
        github_actions_style=github_actions_style,
        github_actions_pin=github_actions_pin,
        concurrency=concurrency,
        request_timeout=request_timeout,
        retries=retries,
        write=write,
        install=install,
        update=update,
        force=force,
    )
    run(root, resolve_config(context, cli_config, project_config), no_retry=no_retry)


if __name__ == "__main__":
    app()
