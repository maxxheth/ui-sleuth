"""Multi-page site crawler and cross-page design system synthesizer."""

from __future__ import annotations

import collections
import datetime
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse, urlunparse

from lxml import html

from ui_sleuth.models import (
    ColorPalette,
    ColorToken,
    ComponentProp,
    CrawledPageSummary,
    DesignTokens,
    ExternalProductionAsset,
    HeaderScale,
    MultiPageCrawlResult,
    PatternSchema,
    SectionBlueprint,
    SiteBlueprint,
    SiteComponent,
    SiteDesignSystem,
    SophisticatedFeature,
    SpacingElevationTokens,
    TypographyTokens,
)
from ui_sleuth.scrapling_engine import ScraplingEngine

if TYPE_CHECKING:
    from ui_sleuth.proxy_manager import ProxyConfig

logger = logging.getLogger(__name__)

# File extensions to ignore during crawling
IGNORED_EXTENSIONS = {
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".tgz",
    ".rar",
    ".7z",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".mp4",
    ".webm",
    ".mov",
    ".avi",
    ".mp3",
    ".wav",
    ".exe",
    ".dmg",
    ".css",
    ".js",
    ".json",
    ".xml",
    ".csv",
    ".tsv",
    ".txt",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".map",
}

# Tracking query parameters to strip
TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "ref",
    "source",
    "fbclid",
    "gclid",
    "msclkid",
    "_ga",
    "_gl",
    "mc_cid",
    "mc_eid",
}


def slugify_url(url: str) -> str:
    """Generate a clean, filesystem-safe directory slug from a URL."""
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if not path:
        return "index"

    # Replace slashes and unsafe characters with hyphens
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", path)
    slug = slug.strip("-").lower()
    return slug or "index"


def normalize_url(url: str, base_url: str) -> str | None:
    """Normalize, filter, and validate internal links for crawling."""
    if not url:
        return None

    # Skip non-web protocols
    url_lower = url.strip().lower()
    if url_lower.startswith(("mailto:", "tel:", "javascript:", "whatsapp:", "sms:", "#")):
        return None

    # Resolve relative URLs
    try:
        resolved = urljoin(base_url, url)
        parsed = urlparse(resolved)
    except Exception:
        return None

    if parsed.scheme not in ("http", "https"):
        return None

    # Compare hostnames (allow matching with or without 'www.')
    base_host = urlparse(base_url).netloc.lower().removeprefix("www.")
    target_host = parsed.netloc.lower().removeprefix("www.")
    if base_host != target_host:
        return None

    # Check file extension
    path_lower = parsed.path.lower()
    for ext in IGNORED_EXTENSIONS:
        if path_lower.endswith(ext):
            return None

    # Filter out tracking query parameters
    query_parts = []
    if parsed.query:
        for pair in parsed.query.split("&"):
            if "=" in pair:
                key, _ = pair.split("=", 1)
                if key.lower() not in TRACKING_PARAMS:
                    query_parts.append(pair)
            else:
                if pair.lower() not in TRACKING_PARAMS:
                    query_parts.append(pair)

    new_query = "&".join(query_parts)

    # Rebuild normalized URL without fragments
    clean_path = parsed.path or "/"
    if clean_path != "/" and clean_path.endswith("/"):
        clean_path = clean_path.rstrip("/")

    normalized = urlunparse((parsed.scheme, parsed.netloc, clean_path, "", new_query, ""))
    return normalized


def extract_links_from_html(html_text: str, base_url: str) -> list[tuple[str, str, int]]:
    """Extract and prioritize internal links from raw HTML.

    Returns a list of tuples: (normalized_url, anchor_text, priority_score).
    """
    if not html_text:
        return []

    try:
        doc = html.fromstring(html_text)
    except Exception as e:
        logger.debug("Failed to parse HTML for link extraction: %s", e)
        return []

    links: list[tuple[str, str, int]] = []
    seen: set[str] = set()

    for a_elem in doc.xpath("//a[@href]"):
        href = a_elem.get("href")
        if not href:
            continue

        norm_url = normalize_url(href, base_url)
        if not norm_url or norm_url in seen:
            continue

        seen.add(norm_url)
        anchor_text = " ".join("".join(a_elem.itertext()).split())

        # Determine structural context
        score = 50  # Default base priority
        parent_tags = [anc.tag.lower() for anc in a_elem.iterancestors() if hasattr(anc, "tag")]

        if "nav" in parent_tags or "header" in parent_tags:
            score = 100
        elif "footer" in parent_tags:
            score = 40

        # Architectural path scoring bonuses
        path_lower = urlparse(norm_url).path.lower()
        if any(k in path_lower for k in ("/pricing", "/plans", "/cost")):
            score += 50
        elif any(k in path_lower for k in ("/features", "/product", "/solutions")):
            score += 40
        elif any(k in path_lower for k in ("/about", "/company", "/team")):
            score += 30
        elif any(k in path_lower for k in ("/docs", "/components", "/showcase", "/case-studies")):
            score += 35
        elif any(k in path_lower for k in ("/contact", "/faq", "/help")):
            score += 20
        elif any(k in path_lower for k in ("/blog", "/news")):
            score += 10

        # De-prioritize pagination and deep article paths
        if re.search(r"/(page|p)/\d+", path_lower) or re.search(r"\bpage=\d+", norm_url):
            score -= 35
        if path_lower.count("/") > 3:
            score -= 20

        links.append((norm_url, anchor_text, score))

    # Sort descending by priority score
    links.sort(key=lambda x: x[2], reverse=True)
    return links


class SiteCrawler:
    """Intelligent multi-page crawler and cross-page design system synthesizer."""

    def __init__(
        self,
        entry_url: str,
        max_pages: int = 5,
        max_depth: int = 2,
        output_dir: Path | None = None,
        wait_until: str = "networkidle",
        timeout: int = 30,
        viewport: str = "1440x900",
        proxy_config: ProxyConfig | None = None,
        use_stealth: bool = False,
        real_chrome: bool | None = None,
        headless: bool = True,
        delay: float = 2.0,
        auto_scroll: bool = True,
        explore_features: bool = True,
        capture_screenshots: bool = False,
    ) -> None:
        self.entry_url = entry_url.strip()
        if not self.entry_url.startswith(("http://", "https://")):
            self.entry_url = f"https://{self.entry_url}"

        normalized_entry = normalize_url(self.entry_url, self.entry_url)
        if normalized_entry:
            self.entry_url = normalized_entry

        self.max_pages = max(1, max_pages)
        self.max_depth = max(0, max_depth)
        self.output_dir = output_dir or Path("./output")
        self.wait_until = wait_until
        self.timeout = timeout
        self.viewport = viewport
        self.proxy_config = proxy_config
        self.use_stealth = use_stealth
        self.real_chrome = real_chrome
        self.headless = headless
        self.delay = delay
        self.auto_scroll = auto_scroll
        self.explore_features = explore_features
        self.capture_screenshots = capture_screenshots

    def crawl(self) -> MultiPageCrawlResult:
        """Execute multi-page crawl across site and synthesize global design system."""
        visited: set[str] = set()
        queued: set[str] = {self.entry_url}
        page_blueprints: dict[str, SiteBlueprint] = {}

        # Queue items: (priority_score, depth, url)
        # Entry URL has highest priority (1000)
        url_queue: list[tuple[int, int, str]] = [(1000, 0, self.entry_url)]

        logger.info(
            "Starting multi-page crawl for %s (max_pages=%d, max_depth=%d)",
            self.entry_url,
            self.max_pages,
            self.max_depth,
        )

        while url_queue and len(visited) < self.max_pages:
            # Pop highest priority URL
            url_queue.sort(key=lambda x: x[0], reverse=True)
            _, depth, current_url = url_queue.pop(0)

            if current_url in visited:
                continue

            visited.add(current_url)
            slug = slugify_url(current_url)

            # Screenshot setup if requested
            screenshot_path = None
            if self.capture_screenshots:
                screenshot_path = self.output_dir / "pages" / slug / "screenshot.png"

            logger.info(
                "[%d/%d] Crawling page: %s (depth=%d)",
                len(visited),
                self.max_pages,
                current_url,
                depth,
            )

            try:
                engine = ScraplingEngine(
                    url=current_url,
                    wait_until=self.wait_until,
                    timeout=self.timeout,
                    viewport=self.viewport,
                    proxy_config=self.proxy_config,
                    use_stealth=self.use_stealth,
                    real_chrome=self.real_chrome,
                    headless=self.headless,
                    screenshot_path=screenshot_path,
                    delay=self.delay,
                    auto_scroll=self.auto_scroll,
                    explore_features=self.explore_features,
                )
                blueprint = engine.execute()
                page_blueprints[current_url] = blueprint

                # If depth allows, discover more internal links
                if depth < self.max_depth and len(visited) < self.max_pages:
                    # In ScraplingEngine, action_data holds HTML text from fetch
                    raw_html = getattr(engine, "_action_data", {}).get("raw_html", "")
                    if not raw_html:
                        # Extract landmark text or fetch html if accessible
                        raw_html = ""

                    discovered = extract_links_from_html(raw_html, current_url)
                    for link_url, _, score in discovered:
                        if link_url not in visited and link_url not in queued:
                            queued.add(link_url)
                            url_queue.append((score, depth + 1, link_url))

            except Exception as e:
                logger.error("Failed to extract page %s: %s", current_url, e)

        # Synthesize global design system from all collected blueprints
        now_iso = datetime.datetime.now(datetime.UTC).isoformat()
        design_system = self.synthesize_design_system(page_blueprints)

        return MultiPageCrawlResult(
            design_system=design_system,
            pages=page_blueprints,
            crawl_timestamp=now_iso,
        )

    def synthesize_design_system(
        self,
        blueprints: dict[str, SiteBlueprint],
    ) -> SiteDesignSystem:
        """Consolidate design tokens, component systems, and motion runtimes across pages."""
        domain = urlparse(self.entry_url).netloc

        if not blueprints:
            return SiteDesignSystem(
                entry_url=self.entry_url,
                domain=domain,
                total_pages_crawled=0,
                tokens=DesignTokens(),
            )

        # 1. Consolidate Design Tokens
        consolidated_tokens = self._synthesize_tokens(blueprints)

        # 2. Consolidate Global Layout Shells (Navbar, Footer)
        global_components = self._synthesize_layout_shells(blueprints)

        # 3. Consolidate Reusable Modular Components
        reusable_components = self._synthesize_reusable_components(blueprints)

        # 4. Consolidate Motion Runtimes & Sophisticated Features
        motion_runtimes, sophisticated_features = self._synthesize_motion_and_features(blueprints)

        # 5. Consolidate External Assets
        external_assets = self._synthesize_external_assets(blueprints)

        # 6. Build Page Summaries
        page_summaries: list[CrawledPageSummary] = []
        for page_url, bp in blueprints.items():
            slug = slugify_url(page_url)
            parsed_path = urlparse(page_url).path or "/"
            features = (
                [f.name for f in bp.sophisticated_features.features]
                if bp.sophisticated_features
                else []
            )
            pattern_count = sum(len(sec.detected_patterns) for sec in bp.landmarks)

            page_summaries.append(
                CrawledPageSummary(
                    url=page_url,
                    slug=slug,
                    path=parsed_path,
                    title=bp.metadata.title,
                    status_code=bp.metadata.status_code,
                    landmark_count=len(bp.landmarks),
                    pattern_count=pattern_count,
                    features_detected=features,
                    screenshot_path=bp.screenshot_path,
                    blueprint_path=f"pages/{slug}/blueprint.json",
                )
            )

        return SiteDesignSystem(
            entry_url=self.entry_url,
            domain=domain,
            total_pages_crawled=len(blueprints),
            tokens=consolidated_tokens,
            global_components=global_components,
            reusable_components=reusable_components,
            motion_and_runtimes=motion_runtimes,
            sophisticated_features=sophisticated_features,
            external_assets=external_assets,
            pages=page_summaries,
        )

    def _synthesize_tokens(self, blueprints: dict[str, SiteBlueprint]) -> DesignTokens:
        """Merge and deduplicate design tokens across all crawled pages."""
        color_freqs: dict[str, int] = collections.defaultdict(int)
        color_roles: dict[str, list[str]] = collections.defaultdict(list)
        color_rgbs: dict[str, str] = {}
        color_sources: dict[str, str] = {}

        primary_fonts: collections.Counter[str] = collections.Counter()
        secondary_fonts: collections.Counter[str] = collections.Counter()
        base_font_sizes: collections.Counter[str] = collections.Counter()
        base_line_heights: collections.Counter[str] = collections.Counter()
        header_scales_by_tag: dict[str, HeaderScale] = {}

        border_radii_set: set[str] = set()
        border_radii_list: list[str] = []
        box_shadows_set: set[str] = set()
        box_shadows_list: list[str] = []
        custom_props: dict[str, str] = {}

        for bp in blueprints.values():
            tokens = bp.tokens

            # Colors
            for ct in tokens.colors.all_colors:
                hex_val = ct.hex.lower()
                color_freqs[hex_val] += ct.frequency
                color_roles[hex_val].append(ct.role)
                if hex_val not in color_rgbs:
                    color_rgbs[hex_val] = ct.rgb
                if ct.source_element and hex_val not in color_sources:
                    color_sources[hex_val] = ct.source_element

            # Typography
            if tokens.typography.primary_font_family:
                primary_fonts[tokens.typography.primary_font_family] += 1
            if tokens.typography.secondary_font_family:
                secondary_fonts[tokens.typography.secondary_font_family] += 1
            if tokens.typography.base_font_size:
                base_font_sizes[tokens.typography.base_font_size] += 1
            if tokens.typography.base_line_height:
                base_line_heights[tokens.typography.base_line_height] += 1

            for tag, scale in tokens.typography.header_scales.items():
                if tag not in header_scales_by_tag:
                    header_scales_by_tag[tag] = scale

            # Spacing & Elevation
            for r in tokens.spacing_elevation.dominant_border_radii:
                if r not in border_radii_set:
                    border_radii_set.add(r)
                    border_radii_list.append(r)

            for s in tokens.spacing_elevation.box_shadows:
                if s not in box_shadows_set:
                    box_shadows_set.add(s)
                    box_shadows_list.append(s)

            # Custom properties
            custom_props.update(tokens.custom_properties)

        # Categorize consolidated colors
        backgrounds: list[str] = []
        surfaces: list[str] = []
        text: list[str] = []
        accents: list[str] = []
        all_color_tokens: list[ColorToken] = []

        # Sort hex codes by frequency descending
        sorted_hexes = sorted(color_freqs.keys(), key=lambda h: color_freqs[h], reverse=True)

        for hex_val in sorted_hexes:
            roles = color_roles[hex_val]
            # Pick most common role among samples
            dominant_role_tuple = collections.Counter(roles).most_common(1)
            dominant_role = dominant_role_tuple[0][0] if dominant_role_tuple else "unknown"
            # Ensure valid literal
            valid_role = (
                dominant_role
                if dominant_role in ("background", "surface", "text", "accent", "border")
                else "unknown"
            )

            tok = ColorToken(
                hex=hex_val,
                rgb=color_rgbs.get(hex_val, ""),
                role=valid_role,  # type: ignore[arg-type]
                frequency=color_freqs[hex_val],
                source_element=color_sources.get(hex_val),
            )
            all_color_tokens.append(tok)

            if valid_role == "background" and hex_val not in backgrounds:
                backgrounds.append(hex_val)
            elif valid_role == "surface" and hex_val not in surfaces:
                surfaces.append(hex_val)
            elif valid_role == "text" and hex_val not in text:
                text.append(hex_val)
            elif valid_role == "accent" and hex_val not in accents:
                accents.append(hex_val)

        # Resolve typography
        primary_font = (
            primary_fonts.most_common(1)[0][0]
            if primary_fonts
            else "system-ui, -apple-system, sans-serif"
        )
        secondary_font = secondary_fonts.most_common(1)[0][0] if secondary_fonts else None
        base_size = base_font_sizes.most_common(1)[0][0] if base_font_sizes else "16px"
        base_lh = base_line_heights.most_common(1)[0][0] if base_line_heights else "1.5"

        typography = TypographyTokens(
            primary_font_family=primary_font,
            secondary_font_family=secondary_font,
            base_font_size=base_size,
            base_line_height=base_lh,
            header_scales=header_scales_by_tag,
        )

        palette = ColorPalette(
            backgrounds=backgrounds,
            surfaces=surfaces,
            text=text,
            accents=accents,
            all_colors=all_color_tokens,
        )

        spacing_elevation = SpacingElevationTokens(
            dominant_border_radii=border_radii_list,
            box_shadows=box_shadows_list,
        )

        return DesignTokens(
            colors=palette,
            typography=typography,
            spacing_elevation=spacing_elevation,
            custom_properties=custom_props,
        )

    def _synthesize_layout_shells(
        self,
        blueprints: dict[str, SiteBlueprint],
    ) -> list[SiteComponent]:
        """Detect shared structural layout shells (GlobalNavbar, GlobalFooter)."""
        components: list[SiteComponent] = []
        nav_pages: list[str] = []
        nav_actions: list[str] = []
        nav_classes: set[str] = set()

        footer_pages: list[str] = []
        footer_actions: list[str] = []
        footer_classes: set[str] = set()

        for page_url, bp in blueprints.items():
            for sec in bp.landmarks:
                if sec.landmark in ("header", "nav"):
                    if page_url not in nav_pages:
                        nav_pages.append(page_url)
                    nav_actions.extend(sec.interaction.primary_action_labels)
                    nav_classes.update(sec.classes)
                elif sec.landmark == "footer":
                    if page_url not in footer_pages:
                        footer_pages.append(page_url)
                    footer_actions.extend(sec.interaction.primary_action_labels)
                    footer_classes.update(sec.classes)

        # Global Navbar Component
        if nav_pages:
            nav_props = [
                ComponentProp(
                    name="links",
                    prop_type="Array<{ label: string; href: string }>",
                    required=True,
                    description="Primary navigation menu links",
                ),
                ComponentProp(
                    name="actions",
                    prop_type="Array<{ label: string; variant?: 'primary' | 'secondary'; href?: string }>",
                    required=False,
                    description="Header CTA action buttons",
                ),
                ComponentProp(
                    name="sticky",
                    prop_type="boolean",
                    required=False,
                    description="Whether navbar pins to viewport top on scroll",
                ),
            ]
            components.append(
                SiteComponent(
                    name="GlobalNavbar",
                    category="navigation",
                    description="Global top-level navigation shell featuring brand logo, routing links, and primary CTA actions.",
                    occurrences=len(nav_pages),
                    pages_found=nav_pages,
                    suggested_props=nav_props,
                    layout_hint="flex-row items-center justify-between",
                    sample_content={
                        "actions": list(dict.fromkeys(nav_actions))[:4],
                        "classes": list(nav_classes)[:5],
                    },
                    subcomponents=["NavbarBrand", "NavbarMenu", "NavbarActions"],
                )
            )

        # Global Footer Component
        if footer_pages:
            footer_props = [
                ComponentProp(
                    name="columns",
                    prop_type="Array<{ title: string; links: Array<{ label: string; href: string }> }>",
                    required=False,
                    description="Grouped footer navigation link columns",
                ),
                ComponentProp(
                    name="copyright",
                    prop_type="string",
                    required=False,
                    description="Copyright statement and trademark notice",
                ),
                ComponentProp(
                    name="socialLinks",
                    prop_type="Array<{ platform: string; href: string }>",
                    required=False,
                    description="Social media profile links",
                ),
            ]
            components.append(
                SiteComponent(
                    name="GlobalFooter",
                    category="footer",
                    description="Global footer shell containing navigational links, copyright attribution, and secondary utilities.",
                    occurrences=len(footer_pages),
                    pages_found=footer_pages,
                    suggested_props=footer_props,
                    layout_hint="grid or multi-column flex",
                    sample_content={
                        "actions": list(dict.fromkeys(footer_actions))[:4],
                        "classes": list(footer_classes)[:5],
                    },
                    subcomponents=["FooterColumn", "FooterCopyright"],
                )
            )

        return components

    def _synthesize_reusable_components(
        self,
        blueprints: dict[str, SiteBlueprint],
    ) -> list[SiteComponent]:
        """Extract and collate repetitive card patterns and modular sections across pages."""
        pattern_instances: dict[str, list[tuple[str, PatternSchema, str | None]]] = (
            collections.defaultdict(list)
        )

        def harvest_patterns(sec: SectionBlueprint, page_url: str) -> None:
            for pat in sec.detected_patterns:
                pattern_instances[pat.pattern].append((page_url, pat, sec.layout_hint))
            for child in sec.subsections:
                harvest_patterns(child, page_url)

        for page_url, bp in blueprints.items():
            for sec in bp.landmarks:
                harvest_patterns(sec, page_url)

        components: list[SiteComponent] = []

        for pat_name, entries in pattern_instances.items():
            total_count = sum(e[1].instance_count for e in entries)
            pages = list(dict.fromkeys(e[0] for e in entries))
            sample_content = entries[0][1].sample_content if entries else {}
            layout_hint = entries[0][2] if entries else None

            # Infer props from sample content keys
            props: list[ComponentProp] = []
            for key in sample_content:
                val = sample_content[key]
                ts_type = "string"
                if isinstance(val, bool):
                    ts_type = "boolean"
                elif isinstance(val, (int, float)):
                    ts_type = "number"
                elif isinstance(val, list):
                    ts_type = "string[]"
                elif isinstance(val, dict):
                    ts_type = "Record<string, any>"

                props.append(
                    ComponentProp(
                        name=key,
                        prop_type=ts_type,
                        required=key in ("title", "name"),
                        description=f"Field extracted from DOM pattern '{key}'",
                    )
                )

            # Categorize archetype
            pat_lower = pat_name.lower()
            category: Any = "card"
            if "pricing" in pat_lower:
                category = "card"
            elif "testimonial" in pat_lower or "review" in pat_lower:
                category = "carousel"
            elif "accordion" in pat_lower or "faq" in pat_lower:
                category = "disclosure"
            elif "hero" in pat_lower:
                category = "hero"
            elif "form" in pat_lower:
                category = "form"
            elif "banner" in pat_lower or "cta" in pat_lower:
                category = "banner"

            components.append(
                SiteComponent(
                    name=pat_name,
                    category=category,
                    description=f"Repetitive {category} component collapsed across {len(pages)} page(s) with {total_count} total rendered instances.",
                    occurrences=total_count,
                    pages_found=pages,
                    suggested_props=props,
                    layout_hint=layout_hint,
                    sample_content=sample_content,
                    subcomponents=[],
                )
            )

        return components

    def _synthesize_motion_and_features(
        self,
        blueprints: dict[str, SiteBlueprint],
    ) -> tuple[list[str], list[SophisticatedFeature]]:
        """Consolidate motion runtimes and interactive features site-wide."""
        engines_set: set[str] = set()
        features_dict: dict[str, SophisticatedFeature] = {}

        for bp in blueprints.values():
            if bp.sophisticated_features:
                for eng in bp.sophisticated_features.detected_engines:
                    engines_set.add(eng)
                for feat in bp.sophisticated_features.features:
                    # Deduplicate by category + name
                    key = f"{feat.category}:{feat.name}"
                    if key not in features_dict:
                        features_dict[key] = feat

        return sorted(engines_set), list(features_dict.values())

    def _synthesize_external_assets(
        self,
        blueprints: dict[str, SiteBlueprint],
    ) -> list[ExternalProductionAsset]:
        """Deduplicate external production assets discovered site-wide."""
        assets_dict: dict[str, ExternalProductionAsset] = {}

        for bp in blueprints.values():
            for asset in bp.external_production_assets:
                # Key by category + primary direct asset URL or wrapper
                urls = "_".join(sorted(asset.direct_asset_urls))
                key = f"{asset.category}:{urls}:{asset.suggested_react_wrapper}"
                if key not in assets_dict:
                    assets_dict[key] = asset.model_copy(deep=True)
                else:
                    # Union URLs
                    existing = assets_dict[key]
                    merged_urls = list(
                        dict.fromkeys(existing.direct_asset_urls + asset.direct_asset_urls)
                    )
                    existing.direct_asset_urls = merged_urls

        return list(assets_dict.values())
