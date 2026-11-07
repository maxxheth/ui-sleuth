"""UI Sleuth: Reverse-engineering live web pages into token-optimized LLM context blueprints."""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any

from ui_sleuth._version import __version__
from ui_sleuth.crawler import SiteCrawler, extract_links_from_html, normalize_url, slugify_url
from ui_sleuth.exporter import (
    export_blueprint,
    export_site_design_system,
    generate_llm_markdown,
    generate_site_design_system_markdown,
)
from ui_sleuth.models import (
    ColorPalette,
    ColorToken,
    ComponentProp,
    CrawledPageSummary,
    DesignTokens,
    ExternalProductionAsset,
    HeaderScale,
    InteractionMetadata,
    MultiPageCrawlResult,
    PatternSchema,
    SectionBlueprint,
    SiteBlueprint,
    SiteComponent,
    SiteDesignSystem,
    SiteMetadata,
    SpacingElevationTokens,
    TypographyTokens,
)
from ui_sleuth.proxy_manager import ProxyConfig, resolve_proxy_config
from ui_sleuth.scrapling_engine import ScraplingEngine

if TYPE_CHECKING:
    from ui_sleuth.cli import app, main

# Suppress known upstream deprecation warning in lxml 6.x when invoked by Scrapling
warnings.filterwarnings(
    "ignore",
    message=r"The 'strip_cdata' option of HTMLParser\(\) has never done anything",
    category=DeprecationWarning,
    module=r"lxml\.html",
)


def __getattr__(name: str) -> Any:
    """Lazily expose CLI objects so importing the library never loads the CLI."""
    if name in {"app", "main"}:
        from ui_sleuth import cli

        return getattr(cli, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ColorPalette",
    "ColorToken",
    "ComponentProp",
    "CrawledPageSummary",
    "DesignTokens",
    "ExternalProductionAsset",
    "HeaderScale",
    "InteractionMetadata",
    "MultiPageCrawlResult",
    "PatternSchema",
    "ProxyConfig",
    "ScraplingEngine",
    "SectionBlueprint",
    "SiteBlueprint",
    "SiteComponent",
    "SiteCrawler",
    "SiteDesignSystem",
    "SiteMetadata",
    "SpacingElevationTokens",
    "TypographyTokens",
    "__version__",
    "app",
    "export_blueprint",
    "export_site_design_system",
    "extract_links_from_html",
    "generate_llm_markdown",
    "generate_site_design_system_markdown",
    "main",
    "normalize_url",
    "resolve_proxy_config",
    "slugify_url",
]
