"""Unit tests for the multi-page crawler and design system synthesizer."""

import re
from pathlib import Path
from tempfile import TemporaryDirectory

from typer.testing import CliRunner

from ui_sleuth.cli import app
from ui_sleuth.crawler import (
    SiteCrawler,
    SlugRegistry,
    extract_links_from_html,
    normalize_url,
    slugify_url,
)
from ui_sleuth.exporter import export_site_design_system
from ui_sleuth.models import (
    ColorPalette,
    ColorToken,
    DesignTokens,
    ExternalProductionAsset,
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
    assert slugify_url("https://example.com/about-us?test=1") == "about-us-test-1"
    assert slugify_url("https://example.com/" + "a" * 200).startswith("a" * 80 + "-")


def test_slug_registry_unique_for_colliding_urls():
    reg = SlugRegistry()
    urls = [
        "https://example.com/shop?cat=a",
        "https://example.com/shop?cat=b",
        "https://example.com/about-us",
        "https://example.com/about/us",
        "https://example.com/shop-cat-a",
    ]
    slugs = [reg.assign(u) for u in urls]
    assert len(set(slugs)) == len(urls)
    assert reg.assign(urls[0]) == slugs[0]  # stable
    assert all(re.fullmatch(r"[a-z0-9_-]+", s) for s in slugs)


def test_normalize_url():
    base = "https://example.com/home"

    # Valid internal relative links
    assert normalize_url("/pricing", base) == "https://example.com/pricing"
    assert normalize_url("about", base) == "https://example.com/about"

    # www / http / default-port variants collapse onto the base origin
    assert normalize_url("https://www.example.com/features", base) == "https://example.com/features"
    assert normalize_url("http://example.com/features", base) == "https://example.com/features"
    assert normalize_url("https://example.com:443/features", base) == "https://example.com/features"
    assert normalize_url("http://www.example.com:80/", base) == "https://example.com/"
    assert normalize_url("https://example.com:8443/features", base) is None
    assert (
        normalize_url("https://www.example.com/", "https://example.com") == "https://example.com/"
    )
    assert (
        normalize_url("https://example.com/", "https://www.example.com/")
        == "https://www.example.com/"
    )
    assert normalize_url("https://evil.com@example.com.evil.com/", base) is None

    # Query parameter order is irrelevant
    assert normalize_url("/s?b=2&a=1", base) == normalize_url("/s?a=1&b=2", base)
    assert normalize_url("/s?b=2&a=1", base) == "https://example.com/s?a=1&b=2"
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
        assert "Multi-Page UI Reconstruction Brief" in md_content
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


def _patch_engine(monkeypatch, failing_urls):
    from contextlib import contextmanager

    from ui_sleuth import crawler as crawler_mod

    @contextmanager
    def fake_session(url, **kwargs):
        yield object()

    monkeypatch.setattr(crawler_mod, "browser_session", fake_session)

    class FakeEngine:
        def __init__(self, url, **kwargs):
            self.url = url

        def execute(self):
            if failing_urls is None or self.url in failing_urls:
                raise RuntimeError(f"boom via http://user:secretpw@proxy.local:8080 {'x' * 500}")
            return create_mock_blueprint(self.url, "T", ["#112233"], "Inter")

    monkeypatch.setattr(crawler_mod, "ScraplingEngine", FakeEngine)


def test_crawl_all_pages_fail_records_failures_and_cli_exits_1(monkeypatch, tmp_path):
    _patch_engine(monkeypatch, None)
    result = runner.invoke(
        app, ["crawl", "https://example.com", "-o", str(tmp_path / "o"), "--max-pages", "2"]
    )
    assert result.exit_code == 1
    assert "no pages could be extracted" in result.stdout
    assert "RuntimeError" in result.stdout
    assert "secretpw" not in result.stdout


def test_crawler_failed_pages_are_short_and_redacted(monkeypatch, tmp_path):
    _patch_engine(monkeypatch, None)
    res = SiteCrawler("https://example.com", max_pages=1, output_dir=tmp_path).crawl()
    assert res.design_system.total_pages_crawled == 0
    assert len(res.failed_pages) == 1
    err = res.failed_pages[0].error
    assert err.startswith("RuntimeError:")
    assert "secretpw" not in err
    assert len(err) <= 200


def test_crawl_partial_failure_exit_0_and_manifest(monkeypatch, tmp_path):
    import json

    _patch_engine(monkeypatch, {"https://example.com/bad"})
    crawler = SiteCrawler("https://example.com", max_pages=2, output_dir=tmp_path)
    # Seed a second page into the queue by patching link discovery.
    from ui_sleuth import crawler as crawler_mod

    monkeypatch.setattr(
        crawler_mod,
        "extract_links_from_html",
        lambda html, base: [("https://example.com/bad", "bad", 1.0)],
    )
    monkeypatch.setattr(
        crawler_mod.ScraplingEngine,
        "_action_data",
        {"raw_html": "<a href='/bad'>x</a>"},
        raising=False,
    )
    res = crawler.crawl()
    assert res.design_system.total_pages_crawled == 1
    assert [f.url for f in res.failed_pages] == ["https://example.com/bad"]
    saved = export_site_design_system(res, tmp_path / "out")
    manifest = json.loads(saved["manifest_json"].read_text())
    assert manifest["failed_pages"][0]["url"] == "https://example.com/bad"
    assert "secretpw" not in json.dumps(manifest)


def _seed_links(monkeypatch, links):
    from ui_sleuth import crawler as crawler_mod

    monkeypatch.setattr(
        crawler_mod, "extract_links_from_html", lambda html, base: [(u, "x", 50) for u in links]
    )


def test_crawl_opens_exactly_one_session_and_closes_it(monkeypatch, tmp_path):
    from contextlib import contextmanager

    from ui_sleuth import crawler as crawler_mod

    events = []
    sessions = []

    @contextmanager
    def counting_session(url, **kwargs):
        events.append("open")
        sess = object()
        try:
            yield sess
        finally:
            events.append("close")

    class FakeEngine:
        def __init__(self, url, **kwargs):
            self.url = url
            sessions.append(kwargs["session"])

        def execute(self):
            return create_mock_blueprint(self.url, "T", ["#112233"], "Inter")

    monkeypatch.setattr(crawler_mod, "browser_session", counting_session)
    monkeypatch.setattr(crawler_mod, "ScraplingEngine", FakeEngine)
    _seed_links(monkeypatch, ["https://example.com/a", "https://example.com/b"])
    res = SiteCrawler("https://example.com", max_pages=3, max_depth=1, output_dir=tmp_path).crawl()
    assert res.design_system.total_pages_crawled == 3
    assert events == ["open", "close"]
    assert len(sessions) == 3 and len({id(s) for s in sessions}) == 1


def test_crawl_closes_session_when_page_raises_unexpectedly(monkeypatch, tmp_path):
    from contextlib import contextmanager

    from ui_sleuth import crawler as crawler_mod

    events = []

    @contextmanager
    def counting_session(url, **kwargs):
        events.append("open")
        try:
            yield object()
        finally:
            events.append("close")

    def boom(*a, **k):
        raise KeyboardInterrupt

    class FakeEngine:
        def __init__(self, url, **kwargs):
            pass

        execute = boom

    monkeypatch.setattr(crawler_mod, "browser_session", counting_session)
    monkeypatch.setattr(crawler_mod, "ScraplingEngine", FakeEngine)
    import pytest

    with pytest.raises(KeyboardInterrupt):
        SiteCrawler("https://example.com", output_dir=tmp_path).crawl()
    assert events == ["open", "close"]


def test_crawl_session_start_failure_is_recorded(monkeypatch, tmp_path):
    from ui_sleuth import crawler as crawler_mod

    def broken(url, **kwargs):
        raise RuntimeError("no browser installed")

    monkeypatch.setattr(crawler_mod, "browser_session", broken)
    res = SiteCrawler("https://example.com", output_dir=tmp_path).crawl()
    assert res.design_system.total_pages_crawled == 0
    assert "no browser installed" in res.failed_pages[0].error


def test_crawl_priority_order_and_redirect_dedupe(monkeypatch, tmp_path):
    from ui_sleuth import crawler as crawler_mod

    _patch_engine(monkeypatch, set())
    order = []

    class FakeEngine:
        def __init__(self, url, **kwargs):
            self.url = url
            self.final_url = "https://www.example.com/landing" if url.endswith("/old") else None

        def execute(self):
            order.append(self.url)
            return create_mock_blueprint(self.url, "T", ["#112233"], "Inter")

    monkeypatch.setattr(crawler_mod, "ScraplingEngine", FakeEngine)
    monkeypatch.setattr(
        crawler_mod,
        "extract_links_from_html",
        lambda html, base: [
            ("https://example.com/low", "x", 10),
            ("https://example.com/old", "x", 90),
            ("https://example.com/landing", "x", 80),
            ("https://example.com/tie1", "x", 10),
        ],
    )
    SiteCrawler("https://example.com", max_pages=10, max_depth=1, output_dir=tmp_path).crawl()
    # high score first, ties FIFO, redirect target /landing not fetched again
    assert order == [
        "https://example.com/",
        "https://example.com/old",
        "https://example.com/low",
        "https://example.com/tie1",
    ]


def test_colliding_urls_get_distinct_paths(monkeypatch, tmp_path):
    from ui_sleuth import crawler as crawler_mod

    shots = []

    class FakeEngine:
        def __init__(self, url, **kwargs):
            self.url = url
            self.shot = kwargs.get("screenshot_path")
            shots.append(self.shot)

        def execute(self):
            bp = create_mock_blueprint(self.url, "T", ["#112233"], "Inter")
            bp.screenshot_path = str(self.shot)
            return bp

    _patch_engine(monkeypatch, set())
    monkeypatch.setattr(crawler_mod, "ScraplingEngine", FakeEngine)
    _seed_links(
        monkeypatch,
        [
            "https://example.com/shop?cat=a",
            "https://example.com/shop?cat=b",
            "https://example.com/about-us",
            "https://example.com/about/us",
        ],
    )
    res = SiteCrawler(
        "https://example.com",
        max_pages=5,
        max_depth=1,
        output_dir=tmp_path,
        capture_screenshots=True,
    ).crawl()
    pages = res.design_system.pages
    assert len(pages) == 5
    assert len({p.blueprint_path for p in pages}) == 5
    assert len({p.screenshot_path for p in pages}) == 5
    assert len(set(shots)) == 5
    saved = export_site_design_system(res, tmp_path / "out")
    assert len(list((saved["manifest_json"].parent / "pages").iterdir())) == 5


def test_synthesis_assets_dedupe_by_primary_url_and_canvases_stay_per_page():
    crawler = SiteCrawler("https://example.com")

    def bp_with(url, assets):
        bp = create_mock_blueprint(url, "T", ["#112233"], "Inter")
        bp.external_production_assets = assets
        return bp

    def asset(category, urls):
        return ExternalProductionAsset(
            category=category, direct_asset_urls=urls, suggested_react_wrapper="w"
        )

    ds = crawler.synthesize_design_system(
        {
            "https://example.com/": bp_with(
                "https://example.com/",
                [asset("3d_model", ["https://cdn/a.glb"]), asset("interactive_canvas", [])],
            ),
            "https://example.com/x": bp_with(
                "https://example.com/x",
                [
                    asset("3d_model", ["https://cdn/a.glb", "https://cdn/b.glb"]),
                    asset("interactive_canvas", []),
                ],
            ),
        }
    )
    models = [a for a in ds.external_assets if a.category == "3d_model"]
    canvases = [a for a in ds.external_assets if a.category == "interactive_canvas"]
    assert len(models) == 1
    assert models[0].direct_asset_urls == ["https://cdn/a.glb", "https://cdn/b.glb"]
    assert len(canvases) == 2


def test_synthesis_caps_features_per_category():
    from ui_sleuth.crawler import MAX_FEATURES_PER_CATEGORY

    crawler = SiteCrawler("https://example.com")
    bp = create_mock_blueprint("https://example.com/", "T", ["#112233"], "Inter")
    bp.sophisticated_features = SophisticatedFeaturesSummary(
        features=[
            SophisticatedFeature(
                category="marquee_stream",
                name=f"m{i}",
                selector=f".m{i}",
                description="d",
            )
            for i in range(30)
        ]
        + [SophisticatedFeature(category="custom_cursor", name="c", selector=".c", description="d")]
    )
    ds = crawler.synthesize_design_system({"https://example.com/": bp})
    cats = [f.category for f in ds.sophisticated_features]
    assert cats.count("marquee_stream") == MAX_FEATURES_PER_CATEGORY
    assert cats.count("custom_cursor") == 1
