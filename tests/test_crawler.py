"""Unit tests for the multi-page crawler and design system synthesizer."""

from pathlib import Path
from tempfile import TemporaryDirectory

from typer.testing import CliRunner

from ui_sleuth.cli import app
from ui_sleuth.crawler import (
    SiteCrawler,
    extract_links_from_html,
    normalize_url,
    slugify_url,
)
from ui_sleuth.exporter import export_site_design_system
from ui_sleuth.models import (
    ColorPalette,
    ColorToken,
    DesignTokens,
    HeaderScale,
    InteractionMetadata,
    MultiPageCrawlResult,
    PatternSchema,
    SectionBlueprint,
    SiteBlueprint,
    SiteMetadata,
    SophisticatedFeature,
    SophisticatedFeaturesSummary,
    SpacingElevationTokens,
    TypographyTokens,
)

runner = CliRunner()


def test_slugify_url():
    assert slugify_url("https://example.com") == "index"
    assert slugify_url("https://example.com/") == "index"
    assert slugify_url("https://example.com/pricing") == "pricing"
    assert slugify_url("https://example.com/pricing/") == "pricing"
    assert slugify_url("https://example.com/products/ai-agent") == "products-ai-agent"
    assert slugify_url("https://example.com/about-us?test=1") == "about-us"


def test_normalize_url():
    base = "https://example.com/home"

    # Valid internal relative links
    assert normalize_url("/pricing", base) == "https://example.com/pricing"
    assert normalize_url("about", base) == "https://example.com/about"

    # Domain matching with www
    assert (
        normalize_url("https://www.example.com/features", base)
        == "https://www.example.com/features"
    )
    assert normalize_url("https://otherdomain.com/blog", base) is None

    # Fragments stripped
    assert normalize_url("/pricing#tier-1", base) == "https://example.com/pricing"

    # Tracking parameters stripped, real query kept
    assert (
        normalize_url("/plans?utm_source=twitter&tier=enterprise", base)
        == "https://example.com/plans?tier=enterprise"
    )

    # Non-web protocols rejected
    assert normalize_url("mailto:support@example.com", base) is None
    assert normalize_url("javascript:void(0)", base) is None
    assert normalize_url("tel:+1234567890", base) is None

    # Ignored binary and asset extensions rejected
    assert normalize_url("/downloads/whitepaper.pdf", base) is None
    assert normalize_url("/images/hero.png", base) is None
    assert normalize_url("/videos/bg.mp4", base) is None


def test_extract_links_from_html():
    html_doc = """
    <!DOCTYPE html>
    <html>
    <body>
        <header>
            <nav>
                <a href="/pricing">Pricing</a>
                <a href="/features">Features</a>
                <a href="/about">About Us</a>
            </nav>
        </header>
        <main>
            <a href="/signup">Get Started</a>
            <a href="/blog/page/2">Blog Page 2</a>
        </main>
        <footer>
            <a href="/terms">Terms</a>
            <a href="/privacy">Privacy</a>
        </footer>
    </body>
    </html>
    """
    links = extract_links_from_html(html_doc, "https://example.com")
    urls = [link[0] for link in links]

    assert "https://example.com/pricing" in urls
    assert "https://example.com/features" in urls
    assert "https://example.com/about" in urls
    assert "https://example.com/signup" in urls
    assert "https://example.com/terms" in urls

    # Navigation links should have higher priority score than footer links
    pricing_score = next(score for url, _, score in links if url.endswith("/pricing"))
    terms_score = next(score for url, _, score in links if url.endswith("/terms"))
    assert pricing_score > terms_score


def create_mock_blueprint(
    url: str,
    title: str,
    colors: list[str],
    primary_font: str,
    has_nav: bool = True,
    has_footer: bool = True,
    patterns: list[PatternSchema] | None = None,
) -> SiteBlueprint:
    color_tokens = [
        ColorToken(hex=c, rgb="rgb(0,0,0)", role="accent" if i == 0 else "text", frequency=5)
        for i, c in enumerate(colors)
    ]
    palette = ColorPalette(
        backgrounds=["#ffffff"],
        surfaces=["#f9f9f9"],
        text=[colors[-1]],
        accents=[colors[0]],
        all_colors=color_tokens,
    )
    typography = TypographyTokens(
        primary_font_family=primary_font,
        base_font_size="16px",
        base_line_height="1.5",
        header_scales={
            "h1": HeaderScale(
                tag="h1",
                font_family=primary_font,
                font_size="36px",
                font_weight="700",
                line_height="44px",
            )
        },
    )
    spacing = SpacingElevationTokens(
        dominant_border_radii=["8px", "16px"], box_shadows=["0 4px 6px rgba(0,0,0,0.1)"]
    )

    landmarks: list[SectionBlueprint] = []
    if has_nav:
        landmarks.append(
            SectionBlueprint(
                landmark="header",
                tag_name="header",
                classes=["site-header", "sticky"],
                interaction=InteractionMetadata(primary_action_labels=["Sign In", "Get Started"]),
            )
        )
    if patterns:
        landmarks.append(
            SectionBlueprint(
                landmark="main",
                tag_name="main",
                layout_hint="grid(3 cols)",
                detected_patterns=patterns,
            )
        )
    if has_footer:
        landmarks.append(
            SectionBlueprint(
                landmark="footer",
                tag_name="footer",
                classes=["site-footer"],
                interaction=InteractionMetadata(primary_action_labels=["Contact Us"]),
            )
        )

    sf = SophisticatedFeaturesSummary(
        has_sophisticated_features=True,
        detected_engines=["GSAP ScrollTrigger"],
        features=[
            SophisticatedFeature(
                category="interactive_disclosure",
                name="AccordionFAQ",
                selector="div.faq-group",
                description="Expandable accordion container",
                dimensions={"top": 100, "left": 0, "width": 800, "height": 400},
            )
        ],
    )

    return SiteBlueprint(
        metadata=SiteMetadata(
            url=url,
            title=title,
            timestamp="2026-10-05T12:00:00Z",
            status_code=200,
        ),
        tokens=DesignTokens(
            colors=palette,
            typography=typography,
            spacing_elevation=spacing,
            custom_properties={"--color-brand": "#6366f1"},
        ),
        landmarks=landmarks,
        external_production_assets=[],
        sophisticated_features=sf,
    )


def test_synthesize_site_design_system():
    crawler = SiteCrawler("https://example.com", max_pages=3)

    bp_home = create_mock_blueprint(
        url="https://example.com/",
        title="Acme Cloud Home",
        colors=["#6366f1", "#0f172a"],
        primary_font="Inter, sans-serif",
        patterns=[
            PatternSchema(
                pattern="FeatureCard",
                instance_count=3,
                sample_content={
                    "title": "Fast Inference",
                    "description": "Blazing fast responses.",
                },
            )
        ],
    )

    bp_pricing = create_mock_blueprint(
        url="https://example.com/pricing",
        title="Pricing Plans",
        colors=["#6366f1", "#3b82f6", "#0f172a"],
        primary_font="Inter, sans-serif",
        patterns=[
            PatternSchema(
                pattern="PricingCard",
                instance_count=3,
                sample_content={"title": "Pro Tier", "price": "$49/mo"},
            ),
            PatternSchema(
                pattern="FeatureCard",
                instance_count=3,
                sample_content={
                    "title": "Priority Support",
                    "description": "24/7 dedicated support.",
                },
            ),
        ],
    )

    blueprints = {
        "https://example.com/": bp_home,
        "https://example.com/pricing": bp_pricing,
    }

    ds = crawler.synthesize_design_system(blueprints)

    assert ds.domain == "example.com"
    assert ds.total_pages_crawled == 2
    assert ds.tokens.typography.primary_font_family == "Inter, sans-serif"
    assert "#6366f1" in [ct.hex for ct in ds.tokens.colors.all_colors]

    # Global Navbar & Footer synthesized
    global_names = [comp.name for comp in ds.global_components]
    assert "GlobalNavbar" in global_names
    assert "GlobalFooter" in global_names

    # Reusable components synthesized
    reusable_names = [comp.name for comp in ds.reusable_components]
    assert "FeatureCard" in reusable_names
    assert "PricingCard" in reusable_names

    # FeatureCard should have 6 total occurrences across 2 pages
    feature_comp = next(c for c in ds.reusable_components if c.name == "FeatureCard")
    assert feature_comp.occurrences == 6
    assert len(feature_comp.pages_found) == 2
    assert any(p.name == "title" for p in feature_comp.suggested_props)

    # Motion & sophisticated features
    assert "GSAP ScrollTrigger" in ds.motion_and_runtimes
    assert any(f.name == "AccordionFAQ" for f in ds.sophisticated_features)


def test_export_site_design_system():
    crawler = SiteCrawler("https://example.com", max_pages=2)
    bp_home = create_mock_blueprint(
        url="https://example.com/",
        title="Acme Cloud Home",
        colors=["#6366f1", "#0f172a"],
        primary_font="Inter, sans-serif",
        patterns=[
            PatternSchema(
                pattern="FeatureCard", instance_count=3, sample_content={"title": "Fast Inference"}
            )
        ],
    )
    blueprints = {"https://example.com/": bp_home}
    ds = crawler.synthesize_design_system(blueprints)
    crawl_result = MultiPageCrawlResult(
        design_system=ds,
        pages=blueprints,
        crawl_timestamp="2026-10-05T12:00:00Z",
    )

    with TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        saved = export_site_design_system(crawl_result, out_dir)
        assert "design_system_json" in saved

        assert (out_dir / "site_design_system.json").exists()
        assert (out_dir / "site_design_system.yaml").exists()
        assert (out_dir / "site_design_system.md").exists()
        assert (out_dir / "manifest.json").exists()
        assert (out_dir / "pages" / "index" / "blueprint.json").exists()

        md_content = (out_dir / "site_design_system.md").read_text(encoding="utf-8")
        assert "MULTI-PAGE COMPONENT SYSTEM" in md_content
        assert "GlobalNavbar" in md_content
        assert "FeatureCard" in md_content


def test_cli_crawl_help():
    res = runner.invoke(app, ["crawl", "--help"])
    assert res.exit_code == 0
    assert "Crawl multiple pages of a website" in res.stdout
    assert "--max-pages" in res.stdout
    assert "--max-depth" in res.stdout


def test_cli_extract_crawl_option():
    res = runner.invoke(app, ["extract", "--help"])
    assert res.exit_code == 0
    assert "--crawl" in res.stdout
    assert "--max-pages" in res.stdout


def test_crawler_entry_url_canonicalization():
    crawler = SiteCrawler("https://example.com")
    assert crawler.entry_url == "https://example.com/"
