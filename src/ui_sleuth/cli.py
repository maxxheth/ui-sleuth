"""Command Line Interface for UI Sleuth using Typer."""

from __future__ import annotations

import logging
import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

import typer
from dotenv import find_dotenv, load_dotenv
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from ui_sleuth._version import __version__
from ui_sleuth.crawler import SiteCrawler
from ui_sleuth.exporter import export_blueprint, export_site_design_system
from ui_sleuth.log_redaction import install_scrapling_log_redaction
from ui_sleuth.proxy_manager import ProxyConfig, ProxyConfigError, resolve_proxy_config
from ui_sleuth.scrapling_engine import ScraplingEngine

app = typer.Typer(
    name="ui-sleuth",
    help="UI Sleuth: Reverse-engineer live web pages into ultra-dense, token-optimized LLM context blueprints.",
    no_args_is_help=True,
    add_completion=False,
)

console = Console()


@app.callback()
def app_callback() -> None:
    """Load a ./.env (searched from the working directory) once per CLI invocation."""
    load_dotenv(find_dotenv(usecwd=True))


def _default_output_dir(target_url: str) -> Path:
    """Return output/<host>/ (without a leading www., sanitized) for a target URL."""
    host = (urlparse(target_url).hostname or "").lower().removeprefix("www.")
    host = re.sub(r"[^a-z0-9.-]+", "-", host).strip(".-")
    return Path("output") / (host or "site")


_FORMAT_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "json": ("blueprint.json", "site_design_system.json", "manifest.json"),
    "markdown": ("blueprint.md", "site_design_system.md"),
    "yaml": ("blueprint.yaml", "site_design_system.yaml"),
}


def _warn_stale_artifacts(output: Path, do_json: bool, do_md: bool, do_yaml: bool) -> None:
    """Warn about artifacts of formats not requested this run that exist from earlier runs."""
    skipped = [
        key
        for key, wanted in (("json", do_json), ("markdown", do_md), ("yaml", do_yaml))
        if not wanted
    ]
    stale = [
        output / name
        for key in skipped
        for name in _FORMAT_ARTIFACTS[key]
        if (output / name).exists()
    ]
    if stale:
        console.print(
            "[yellow]Warning:[/yellow] stale artifacts from earlier runs remain in "
            f"{escape(str(output))} (not updated this run, not deleted):"
        )
        for path in stale:
            console.print(f"  - {escape(str(path))}")


class OutputFormat(StrEnum):
    """Accepted values for --format."""

    all = "all"
    json = "json"
    markdown = "markdown"
    md = "md"
    yaml = "yaml"
    yml = "yml"


def _format_flags(fmt: OutputFormat) -> tuple[bool, bool, bool]:
    """Return (json, markdown, yaml) export flags for a format."""
    return (
        fmt in {OutputFormat.all, OutputFormat.json},
        fmt in {OutputFormat.all, OutputFormat.markdown, OutputFormat.md},
        fmt in {OutputFormat.all, OutputFormat.yaml, OutputFormat.yml},
    )


def setup_logging(verbose: bool = False) -> None:
    """Configure console logging level and format."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    install_scrapling_log_redaction()


_PROXY_PARAMS = ("proxy", "proxy_file", "decodo", "proxy_env", "no_proxy")

ProxyOpt = Annotated[
    str | None,
    typer.Option(
        "--proxy",
        envvar="UI_SLEUTH_PROXY",
        show_envvar=True,
        help="Single proxy (scheme://user:pass@host:port, user:pass@host:port, "
        "host:port:user:pass or host:port)",
    ),
]
ProxyFileOpt = Annotated[
    str | None,
    typer.Option(
        "--proxy-file",
        envvar="UI_SLEUTH_PROXY_FILE",
        show_envvar=True,
        help="Path to text file with one proxy per line (same formats)",
    ),
]
DecodoOpt = Annotated[
    bool,
    typer.Option(
        "--decodo",
        envvar="UI_SLEUTH_DECODO",
        show_envvar=True,
        help="Use Decodo settings from DECODO_* env/.env (errors if unusable)",
    ),
]
ProxyEnvOpt = Annotated[
    bool,
    typer.Option(
        "--proxy-env",
        envvar="UI_SLEUTH_PROXY_ENV",
        show_envvar=True,
        help="Use PROXY_FILE / HTTPS_PROXY / HTTP_PROXY from the environment",
    ),
]
NoProxyOpt = Annotated[
    bool,
    typer.Option(
        "--no-proxy",
        envvar="UI_SLEUTH_NO_PROXY",
        show_envvar=True,
        help="Force a direct connection (no proxy)",
    ),
]


def _resolve_proxy_or_exit(
    ctx: typer.Context,
    proxy: str | None,
    proxy_file: str | None,
    decodo: bool,
    proxy_env: bool,
    no_proxy: bool,
) -> ProxyConfig:
    """Resolve proxy options; print a credential-free error and exit 1 on failure.

    Options given on the command line win over UI_SLEUTH_* environment values: if any
    proxy option came from the command line, all environment-sourced ones are dropped.
    """
    values: dict[str, Any] = {
        "proxy": proxy,
        "proxy_file": proxy_file,
        "decodo": decodo,
        "proxy_env": proxy_env,
        "no_proxy": no_proxy,
    }
    # Compare by enum name: Typer may vendor its own Click, so ParameterSource isn't importable
    # from a single stable place.
    sources = {n: getattr(ctx.get_parameter_source(n), "name", None) for n in _PROXY_PARAMS}
    from_env = [n for n, src in sources.items() if src == "ENVIRONMENT"]
    log = logging.getLogger(__name__)
    if any(src == "COMMANDLINE" for src in sources.values()):
        for n in from_env:
            values[n] = None if n in ("proxy", "proxy_file") else False
        if from_env:
            log.info(
                "Ignoring environment proxy options overridden by the command line: %s",
                ", ".join(from_env),
            )
    elif from_env:
        log.info("Proxy options taken from the environment/.env: %s", ", ".join(from_env))
    try:
        cfg = resolve_proxy_config(
            proxy=values["proxy"],
            proxy_file=values["proxy_file"],
            enable_decodo=values["decodo"],
            use_env=values["proxy_env"],
            no_proxy=values["no_proxy"],
        )
    except ProxyConfigError as e:
        console.print(f"[bold red]Proxy configuration error:[/bold red] {escape(str(e))}")
        raise typer.Exit(code=1) from e
    logging.getLogger(__name__).info("Proxy: %s", cfg.get_display_summary() or "direct")
    return cfg


def _execute_multi_page_crawl(
    target_url: str,
    output: Path,
    max_pages: int,
    max_depth: int,
    wait_until: str,
    timeout: int,
    viewport: str,
    proxy_cfg: ProxyConfig,
    stealth: bool,
    real_chrome: bool | None,
    headless: bool,
    delay: float,
    auto_scroll: bool,
    explore: bool,
    screenshot: bool,
    fmt: OutputFormat,
    verbose: bool,
) -> None:
    """Execute multi-page site crawl and display consolidated design system report."""
    console.print(
        Panel.fit(
            f"[bold cyan]ui-sleuth[/bold cyan] [green]Multi-Page Site Crawl[/green]\n"
            f"Target: [bold]{target_url}[/bold]\n"
            f"Max Pages: [yellow]{max_pages}[/yellow] | Max Depth: [yellow]{max_depth}[/yellow]\n"
            f"Output: [yellow]{output}[/yellow]\n"
            f"Wait condition: [magenta]{wait_until}[/magenta] | Viewport: [blue]{viewport}[/blue]",
            title="Design System & Component Crawler",
        )
    )

    if proxy_cfg.has_proxy:
        console.print(f"[cyan]Using Proxy:[/cyan] {proxy_cfg.get_display_summary()}")

    crawler = SiteCrawler(
        entry_url=target_url,
        max_pages=max_pages,
        max_depth=max_depth,
        output_dir=output,
        wait_until=wait_until,
        timeout=timeout,
        viewport=viewport,
        proxy_config=proxy_cfg,
        use_stealth=stealth,
        real_chrome=real_chrome,
        headless=headless,
        delay=delay,
        auto_scroll=auto_scroll,
        explore_features=explore,
        capture_screenshots=screenshot,
    )

    try:
        with console.status(
            f"[bold green]Crawling up to {max_pages} pages and synthesizing design system..."
        ):
            crawl_result = crawler.crawl()
    except Exception as e:
        console.print(f"[bold red]Crawl failed:[/bold red] {e}")
        if verbose:
            console.print_exception()
        raise typer.Exit(code=1) from e

    failures = crawl_result.failed_pages
    if crawl_result.design_system.total_pages_crawled == 0:
        console.print(
            f"[bold red]Crawl failed:[/bold red] no pages could be extracted from {escape(target_url)}"
        )
        for fp in failures:
            console.print(f"  - {escape(fp.url)}: {escape(fp.error)}")
        raise typer.Exit(code=1)

    do_json, do_md, do_yaml = _format_flags(fmt)
    _warn_stale_artifacts(output, do_json, do_md, do_yaml)

    saved = export_site_design_system(
        crawl_result=crawl_result,
        output_dir=output,
        export_json=do_json,
        export_markdown=do_md,
        export_yaml=do_yaml,
    )

    ds = crawl_result.design_system

    # Table 1: Crawled Pages Table
    pages_table = Table(title="Crawled Page Inventory", show_header=True)
    pages_table.add_column("Route", style="cyan")
    pages_table.add_column("Page Title", style="white")
    pages_table.add_column("HTTP", style="green")
    pages_table.add_column("Landmarks", justify="right", style="yellow")
    pages_table.add_column("Patterns", justify="right", style="magenta")

    for p in ds.pages:
        pages_table.add_row(
            p.path,
            p.title or "N/A",
            str(p.status_code),
            str(p.landmark_count),
            str(p.pattern_count),
        )
    console.print(pages_table)

    # Table 2: Synthesized Component Library
    comp_table = Table(title="Synthesized Component System", show_header=True)
    comp_table.add_column("Component", style="cyan")
    comp_table.add_column("Archetype", style="green")
    comp_table.add_column("Occurrences", justify="right", style="yellow")
    comp_table.add_column("Pages Used", style="magenta")

    for comp in ds.global_components:
        comp_table.add_row(
            f"<{comp.name} />",
            f"Layout Shell ({comp.category})",
            str(comp.occurrences),
            f"{len(comp.pages_found)} pages",
        )

    for comp in ds.reusable_components:
        comp_table.add_row(
            f"<{comp.name} />",
            comp.category,
            str(comp.occurrences),
            f"{len(comp.pages_found)} pages",
        )
    console.print(comp_table)

    console.print("\n[bold green]Design System Artifacts successfully saved:[/bold green]")
    for kind, path in saved.items():
        console.print(f"  • [{kind.upper()}] [link=file://{path.resolve()}]{path}[/link]")

    if failures:
        console.print(
            f"\n[bold yellow]Warning:[/bold yellow] {len(failures)} page(s) failed "
            f"({crawl_result.design_system.total_pages_crawled} succeeded):"
        )
        for fp in failures:
            console.print(f"  - {escape(fp.url)}: {escape(fp.error)}")


@app.command()
def extract(
    ctx: typer.Context,
    url: Annotated[
        str | None,
        typer.Argument(help="Target website URL to reverse-engineer"),
    ] = None,
    url_opt: Annotated[
        str | None,
        typer.Option("--url", "-u", help="Target URL (alternative to positional argument)"),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Output directory for generated blueprints [default: output/<host>]",
        ),
    ] = None,
    screenshot: Annotated[
        bool,
        typer.Option(
            "--screenshot", "-s", help="Capture a full-page screenshot alongside the blueprint"
        ),
    ] = False,
    wait_until: Annotated[
        str,
        typer.Option(
            "--wait-until",
            help="Wait condition for page stability: 'networkidle', 'load', or a CSS selector",
        ),
    ] = "networkidle",
    timeout: Annotated[
        int,
        typer.Option("--timeout", "-t", help="Timeout in seconds for page fetching and rendering"),
    ] = 30,
    viewport: Annotated[
        str,
        typer.Option("--viewport", help="Emulated viewport dimensions (WIDTHxHEIGHT)"),
    ] = "1440x900",
    proxy: ProxyOpt = None,
    proxy_file: ProxyFileOpt = None,
    decodo: DecodoOpt = False,
    proxy_env: ProxyEnvOpt = False,
    no_proxy: NoProxyOpt = False,
    stealth: Annotated[
        bool,
        typer.Option(
            "--stealth", help="Use StealthyFetcher to bypass Cloudflare and bot mitigations"
        ),
    ] = False,
    fmt: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            "-f",
            help="Output format: all, json, markdown (alias: md), yaml (alias: yml)",
        ),
    ] = OutputFormat.all,
    headless: Annotated[
        bool,
        typer.Option("--headless/--no-headless", help="Run browser in headless or visible mode"),
    ] = True,
    real_chrome: Annotated[
        bool | None,
        typer.Option(
            "--real-chrome/--no-real-chrome", help="Use installed Google Chrome executable"
        ),
    ] = None,
    delay: Annotated[
        float,
        typer.Option(
            "--delay",
            "-d",
            help="Post-load stabilization delay in seconds for animations and dynamic content",
        ),
    ] = 2.0,
    auto_scroll: Annotated[
        bool,
        typer.Option(
            "--auto-scroll/--no-auto-scroll",
            help="Progressively scroll the page to trigger lazy loading, fonts, and scroll triggers",
        ),
    ] = True,
    explore: Annotated[
        bool,
        typer.Option(
            "--explore/--no-explore",
            help="Reserved for future interaction exploration; currently has no effect (features are always detected, never expanded or clicked)",
        ),
    ] = True,
    crawl: Annotated[
        bool,
        typer.Option(
            "--crawl",
            help="Enable multi-page site crawl to gather component, motion, and layout evidence across the site",
        ),
    ] = False,
    max_pages: Annotated[
        int,
        typer.Option(
            "--max-pages",
            "-m",
            help="Maximum number of pages to crawl when multi-page crawl is enabled [default: 1 for single, 5 for crawl]",
        ),
    ] = 1,
    max_depth: Annotated[
        int,
        typer.Option(
            "--max-depth",
            help="Maximum crawl link depth for internal links [default: 2]",
        ),
    ] = 2,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable verbose debug logging"),
    ] = False,
) -> None:
    """Reverse-engineer a web page into a token-optimized blueprint for vibe-coding."""
    setup_logging(verbose)

    target_url = url or url_opt
    if not target_url:
        console.print(
            "[bold red]Error:[/bold red] Missing target URL. Specify as argument or via --url."
        )
        raise typer.Exit(code=1)

    if not target_url.startswith(("http://", "https://")):
        target_url = f"https://{target_url}"

    if output is None:
        output = _default_output_dir(target_url)

    proxy_cfg = _resolve_proxy_or_exit(
        ctx,
        proxy=proxy,
        proxy_file=proxy_file,
        decodo=decodo,
        proxy_env=proxy_env,
        no_proxy=no_proxy,
    )

    # Multi-page crawl activation
    if crawl and max_pages == 1:
        max_pages = 5

    if max_pages > 1 or crawl:
        _execute_multi_page_crawl(
            target_url=target_url,
            output=output,
            max_pages=max_pages,
            max_depth=max_depth,
            wait_until=wait_until,
            timeout=timeout,
            viewport=viewport,
            proxy_cfg=proxy_cfg,
            stealth=stealth,
            real_chrome=real_chrome,
            headless=headless,
            delay=delay,
            auto_scroll=auto_scroll,
            explore=explore,
            screenshot=screenshot,
            fmt=fmt,
            verbose=verbose,
        )
        return

    console.print(
        Panel.fit(
            f"[bold cyan]ui-sleuth[/bold cyan] [green]v{__version__}[/green]\n"
            f"Target: [bold]{target_url}[/bold]\n"
            f"Output: [yellow]{output}[/yellow]\n"
            f"Wait condition: [magenta]{wait_until}[/magenta] | Viewport: [blue]{viewport}[/blue]",
            title="UI Reverse Engineering Pipeline",
        )
    )

    if proxy_cfg.has_proxy:
        console.print(f"[cyan]Using Proxy:[/cyan] {proxy_cfg.get_display_summary()}")

    # 2. Configure screenshot destination
    screenshot_dest: Path | None = None
    if screenshot:
        output.mkdir(parents=True, exist_ok=True)
        screenshot_dest = output / "screenshot.png"

    # 3. Initialize and execute Scrapling engine
    engine = ScraplingEngine(
        url=target_url,
        wait_until=wait_until,
        timeout=timeout,
        viewport=viewport,
        proxy_config=proxy_cfg,
        use_stealth=stealth,
        real_chrome=real_chrome,
        headless=headless,
        screenshot_path=screenshot_dest,
        delay=delay,
        auto_scroll=auto_scroll,
        explore_features=explore,
    )

    try:
        with console.status("[bold green]Harvesting design tokens and mapping DOM landmarks..."):
            blueprint = engine.execute()
    except Exception as e:
        console.print(f"[bold red]Extraction failed:[/bold red] {e}")
        if verbose:
            console.print_exception()
        raise typer.Exit(code=1) from e

    # 4. Export blueprint artifacts
    do_json, do_md, do_yaml = _format_flags(fmt)
    _warn_stale_artifacts(output, do_json, do_md, do_yaml)

    saved = export_blueprint(
        blueprint=blueprint,
        output_dir=output,
        export_json=do_json,
        export_markdown=do_md,
        export_yaml=do_yaml,
    )

    # 5. Display summary report
    table = Table(title="Generated Blueprint Summary", show_header=True)
    table.add_column("Category", style="cyan")
    table.add_column("Count / Details", style="green")

    table.add_row("Page Title", blueprint.metadata.title or "N/A")
    table.add_row("Unique Colors", str(len(blueprint.tokens.colors.all_colors)))
    table.add_row("Primary Font", blueprint.tokens.typography.primary_font_family)
    table.add_row("Header Scales", str(len(blueprint.tokens.typography.header_scales)))
    table.add_row("DOM Landmarks", str(len(blueprint.landmarks)))

    # Count collapsed patterns across all landmarks
    total_patterns = sum(len(lm.detected_patterns) for lm in blueprint.landmarks)
    table.add_row("Collapsed Patterns", str(total_patterns))

    table.add_row("Production Assets", str(len(blueprint.external_production_assets)))

    if (
        blueprint.sophisticated_features
        and blueprint.sophisticated_features.has_sophisticated_features
    ):
        sf = blueprint.sophisticated_features
        engines_str = ", ".join(sf.detected_engines) if sf.detected_engines else "Standard DOM"
        table.add_row("Motion Engines", engines_str)
        table.add_row("Sophisticated Features", f"{len(sf.features)} analyzed")

    console.print(table)

    console.print("\n[bold green]Artifacts successfully saved:[/bold green]")
    for kind, path in saved.items():
        console.print(f"  • [{kind.upper()}] [link=file://{path.resolve()}]{path}[/link]")
    if blueprint.screenshot_path:
        console.print(
            f"  • [SCREENSHOT] [link=file://{Path(blueprint.screenshot_path).resolve()}]{blueprint.screenshot_path}[/link]"
        )


@app.command()
def crawl(
    ctx: typer.Context,
    url: Annotated[
        str | None,
        typer.Argument(help="Target website URL to crawl"),
    ] = None,
    url_opt: Annotated[
        str | None,
        typer.Option("--url", "-u", help="Target URL (alternative to positional argument)"),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Output directory for generated blueprints [default: output/<host>]",
        ),
    ] = None,
    max_pages: Annotated[
        int,
        typer.Option(
            "--max-pages",
            "-m",
            help="Maximum number of pages to crawl across the site",
        ),
    ] = 5,
    max_depth: Annotated[
        int,
        typer.Option(
            "--max-depth",
            help="Maximum crawl link depth for internal link discovery",
        ),
    ] = 2,
    screenshot: Annotated[
        bool,
        typer.Option(
            "--screenshot", "-s", help="Capture full-page screenshots alongside blueprints"
        ),
    ] = False,
    wait_until: Annotated[
        str,
        typer.Option(
            "--wait-until",
            help="Wait condition for page stability: 'networkidle', 'load', or a CSS selector",
        ),
    ] = "networkidle",
    timeout: Annotated[
        int,
        typer.Option("--timeout", "-t", help="Timeout in seconds for page fetching and rendering"),
    ] = 30,
    viewport: Annotated[
        str,
        typer.Option("--viewport", help="Emulated viewport dimensions (WIDTHxHEIGHT)"),
    ] = "1440x900",
    proxy: ProxyOpt = None,
    proxy_file: ProxyFileOpt = None,
    decodo: DecodoOpt = False,
    proxy_env: ProxyEnvOpt = False,
    no_proxy: NoProxyOpt = False,
    stealth: Annotated[
        bool,
        typer.Option(
            "--stealth", help="Use StealthyFetcher to bypass Cloudflare and bot mitigations"
        ),
    ] = False,
    fmt: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            "-f",
            help="Output format: all, json, markdown (alias: md), yaml (alias: yml)",
        ),
    ] = OutputFormat.all,
    headless: Annotated[
        bool,
        typer.Option("--headless/--no-headless", help="Run browser in headless or visible mode"),
    ] = True,
    real_chrome: Annotated[
        bool | None,
        typer.Option(
            "--real-chrome/--no-real-chrome", help="Use installed Google Chrome executable"
        ),
    ] = None,
    delay: Annotated[
        float,
        typer.Option(
            "--delay",
            "-d",
            help="Post-load stabilization delay in seconds for animations and dynamic content",
        ),
    ] = 2.0,
    auto_scroll: Annotated[
        bool,
        typer.Option(
            "--auto-scroll/--no-auto-scroll",
            help="Progressively scroll the page to trigger lazy loading, fonts, and scroll triggers",
        ),
    ] = True,
    explore: Annotated[
        bool,
        typer.Option(
            "--explore/--no-explore",
            help="Reserved for future interaction exploration; currently has no effect (features are always detected, never expanded or clicked)",
        ),
    ] = True,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable verbose debug logging"),
    ] = False,
) -> None:
    """Crawl multiple pages of a website to synthesize a comprehensive design system and component library."""
    setup_logging(verbose)

    target_url = url or url_opt
    if not target_url:
        console.print(
            "[bold red]Error:[/bold red] Missing target URL. Specify as argument or via --url."
        )
        raise typer.Exit(code=1)

    if not target_url.startswith(("http://", "https://")):
        target_url = f"https://{target_url}"

    if output is None:
        output = _default_output_dir(target_url)

    proxy_cfg = _resolve_proxy_or_exit(
        ctx,
        proxy=proxy,
        proxy_file=proxy_file,
        decodo=decodo,
        proxy_env=proxy_env,
        no_proxy=no_proxy,
    )

    _execute_multi_page_crawl(
        target_url=target_url,
        output=output,
        max_pages=max_pages,
        max_depth=max_depth,
        wait_until=wait_until,
        timeout=timeout,
        viewport=viewport,
        proxy_cfg=proxy_cfg,
        stealth=stealth,
        real_chrome=real_chrome,
        headless=headless,
        delay=delay,
        auto_scroll=auto_scroll,
        explore=explore,
        screenshot=screenshot,
        fmt=fmt,
        verbose=verbose,
    )


@app.command()
def version() -> None:
    """Show the ui-sleuth version."""
    console.print(f"[bold cyan]ui-sleuth[/bold cyan] version [green]{__version__}[/green]")


def main() -> None:
    """CLI application entrypoint."""
    app()


if __name__ == "__main__":
    main()
