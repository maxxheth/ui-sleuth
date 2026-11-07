"""Strict Pydantic data models for UI Sleuth specifications."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ColorToken(BaseModel):
    """Normalized color token extracted from DOM computed styles."""

    hex: str = Field(
        description="Normalized lowercase hex color: #rrggbb, or #rrggbbaa when the source color is translucent"
    )
    rgb: str = Field(description="Normalized rgb/rgba string")
    role: Literal["background", "surface", "text", "accent", "border", "unknown"] = Field(
        default="unknown",
        description="Inferred semantic design token role",
    )
    frequency: int = Field(default=1, description="Usage frequency count across probed landmarks")
    source_element: str | None = Field(
        default=None,
        description="Landmark or element tag where this color was first/predominantly sampled",
    )


class ColorPalette(BaseModel):
    """Deduplicated and semantically categorized color palette."""

    backgrounds: list[str] = Field(
        default_factory=list,
        description="Hex colors predominantly used for body/landmark backgrounds",
    )
    surfaces: list[str] = Field(
        default_factory=list,
        description="Hex colors used for cards, modals, dropdowns, and surfaces",
    )
    text: list[str] = Field(
        default_factory=list,
        description="Hex colors used for primary, secondary, and heading text",
    )
    accents: list[str] = Field(
        default_factory=list,
        description="Hex colors used for CTAs, active states, and highlights",
    )
    all_colors: list[ColorToken] = Field(
        default_factory=list,
        description="Full list of deduplicated color tokens with metadata",
    )


class HeaderScale(BaseModel):
    """Typography metrics for a specific heading scale level."""

    tag: str = Field(description="Heading tag (e.g. 'h1', 'h2', 'h3', 'h4')")
    font_family: str = Field(description="Resolved font family stack")
    font_size: str = Field(description="Computed font size (e.g. '36px' / '2.25rem')")
    font_weight: str = Field(description="Computed font weight (e.g. '700')")
    line_height: str = Field(description="Computed line height (e.g. '44px' / '1.2')")
    letter_spacing: str | None = Field(
        default=None,
        description="Computed letter spacing if non-normal",
    )


class TypographyTokens(BaseModel):
    """Extracted typography system tokens."""

    primary_font_family: str = Field(
        description="Primary body and interface font family stack",
    )
    secondary_font_family: str | None = Field(
        default=None,
        description="Secondary/heading or monospace font family if distinct",
    )
    base_font_size: str = Field(
        default="16px",
        description="Base body font size (px or rem)",
    )
    base_line_height: str = Field(
        default="1.5",
        description="Base line height ratio or px value",
    )
    header_scales: dict[str, HeaderScale] = Field(
        default_factory=dict,
        description="Mapping of heading tags (h1-h4) to their typography scales",
    )


class SpacingElevationTokens(BaseModel):
    """Spacing, radius, and elevation tokens."""

    dominant_border_radii: list[str] = Field(
        default_factory=list,
        description="Dominant border-radius values sampled from buttons, cards, inputs",
    )
    box_shadows: list[str] = Field(
        default_factory=list,
        description="Dominant box-shadow signatures used across buttons and elevation surfaces",
    )


class DesignTokens(BaseModel):
    """Complete design token harvest for downstream vibe-coding."""

    colors: ColorPalette = Field(default_factory=ColorPalette)
    typography: TypographyTokens = Field(
        default_factory=lambda: TypographyTokens(
            primary_font_family="system-ui, -apple-system, sans-serif",
            base_font_size="16px",
            base_line_height="1.5",
        )
    )
    spacing_elevation: SpacingElevationTokens = Field(default_factory=SpacingElevationTokens)
    custom_properties: dict[str, str] = Field(
        default_factory=dict,
        description="CSS custom properties defined on :root matching --color-* or --theme-*",
    )


class InteractionMetadata(BaseModel):
    """Actionable user interaction counters and action labels for a section."""

    inputs_count: int = Field(default=0, description="Count of standard input elements")
    forms_count: int = Field(default=0, description="Count of form elements")
    textareas_count: int = Field(default=0, description="Count of textarea elements")
    selects_count: int = Field(default=0, description="Count of select dropdowns")
    primary_actions_count: int = Field(
        default=0,
        description="Count of primary action buttons / submit buttons / CTA anchors",
    )
    primary_action_labels: list[str] = Field(
        default_factory=list,
        description="Concise labels of primary CTAs and action buttons",
    )


class PatternSchema(BaseModel):
    """Collapsed repetitive structural pattern schema (e.g. card grids, testimonial rows)."""

    pattern: str = Field(
        description="Inferred pattern name (e.g. 'FeatureCard', 'TestimonialCard', 'PricingCard')",
    )
    instance_count: int = Field(
        description="Total number of identical sibling instances collapsed",
    )
    sample_content: dict[str, Any] = Field(
        default_factory=dict,
        description="Representative sample content extracted from the first instances",
    )


class SectionBlueprint(BaseModel):
    """Semantic pruned structural blueprint of a landmark or section."""

    landmark: str = Field(
        description="Structural landmark role ('header', 'nav', 'main', 'section', 'article', 'aside', 'footer')",
    )
    tag_name: str = Field(description="HTML tag name of this landmark node")
    id: str | None = Field(default=None, description="DOM ID attribute if present")
    classes: list[str] = Field(
        default_factory=list,
        description="Cleaned, non-utility CSS classes or primary identifiers",
    )
    layout_hint: str | None = Field(
        default=None,
        description="Inferred layout pattern (e.g. 'grid(3 cols)', 'flex-row', 'flex-col', 'stack')",
    )
    text_preview: str = Field(
        default="",
        description="Concise text preview (max 15-25 words) documenting copy intent",
    )
    interaction: InteractionMetadata = Field(default_factory=InteractionMetadata)
    detected_patterns: list[PatternSchema] = Field(
        default_factory=list,
        description="Repetitive component patterns collapsed within this section",
    )
    subsections: list[SectionBlueprint] = Field(
        default_factory=list,
        description="Nested structural subsections or child landmarks",
    )


class ExternalProductionAsset(BaseModel):
    """External runtime asset (3D model, Rive/Lottie animation, rich video loop, WebGL canvas)."""

    category: Literal["3d_model", "vector_motion", "rich_video", "interactive_canvas"] = Field(
        description="Production asset discipline category",
    )
    detected_engines: list[str] = Field(
        default_factory=list,
        description="Detected runtime engines (e.g. 'Three.js', 'Babylon.js', 'Spline', 'Rive', 'Lottie', 'HTML5 Video')",
    )
    direct_asset_urls: list[str] = Field(
        default_factory=list,
        description="Discovered asset URLs (.gltf, .glb, .riv, .lottie, .mp4, etc.)",
    )
    parent_landmark: str = Field(
        default="unknown",
        description="Landmark or structural selector where the asset/canvas is mounted",
    )
    suggested_react_wrapper: str = Field(
        description="Recommended React / Next.js ecosystem wrapper library or component pattern",
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional technical diagnostics (e.g. autoplay/muted/loop flags, WebGL version, canvas size)",
    )


class SophisticatedFeature(BaseModel):
    """An advanced interactive or motion component requiring dedicated analysis."""

    category: Literal[
        "sticky_scroll_track",
        "offscreen_drawer",
        "interactive_disclosure",
        "tab_container",
        "webgl_canvas",
        "motion_physics_engine",
        "marquee_stream",
        "magnetic_physics",
        "custom_cursor",
        "typographic_reveal",
        "hover_media_switcher",
        "scroll_parallax",
    ] = Field(description="Category of the sophisticated feature")
    name: str = Field(description="Human-readable feature name or landmark identifier")
    selector: str = Field(description="CSS selector identifying the component root")
    description: str = Field(description="Architectural description and interaction mechanics")
    dimensions: dict[str, int] = Field(
        default_factory=dict,
        description="Rendered dimensions: top, left, width, height",
    )
    requires_independent_exploration: bool = Field(
        default=True,
        description="Whether this feature needs isolated or phased exploration rather than flat capture",
    )
    exploration_status: Literal["detected", "explored", "skipped"] = Field(
        default="detected",
        description="Execution status of targeted exploration",
    )
    suggested_implementation: str = Field(
        default="",
        description="Recommended modern React/Next.js/Framer Motion pattern to replicate this feature",
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Extracted runtime diagnostics (e.g. keyframe phases, triggers, expanded content)",
    )


class SophisticatedFeaturesSummary(BaseModel):
    """Aggregated manifest of sophisticated interactive systems and recommended explorations."""

    has_sophisticated_features: bool = Field(
        default=False,
        description="Flag indicating if the site employs advanced animation or interaction systems",
    )
    detected_engines: list[str] = Field(
        default_factory=list,
        description="Detected motion/physics/3D libraries (e.g. 'GSAP ScrollTrigger', 'Lenis Smooth Scroll', 'Three.js')",
    )
    features: list[SophisticatedFeature] = Field(
        default_factory=list,
        description="Individual sophisticated components detected on the page",
    )
    recommended_explorations: list[str] = Field(
        default_factory=list,
        description="Actionable recommendations for how an LLM or UI engineer should reconstruct these systems",
    )


class SiteMetadata(BaseModel):
    """Metadata regarding the scraped target page and execution session."""

    url: str = Field(description="Entry URL scraped")
    title: str = Field(default="", description="Resolved page title")
    description: str | None = Field(default=None, description="Meta description tag content")
    viewport: dict[str, int] = Field(
        default_factory=lambda: {"width": 1440, "height": 900},
        description="Emulated viewport screen dimensions",
    )
    timestamp: str = Field(description="ISO 8601 execution timestamp")
    status_code: int = Field(default=200, description="HTTP response status code")
    wait_condition_used: str = Field(
        default="networkidle",
        description="Wait condition applied during navigation",
    )
    proxy_used: str | None = Field(
        default=None,
        description="Redacted proxy endpoint used (if proxy was configured)",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal extraction step failures (step name and short error)",
    )


class SiteBlueprint(BaseModel):
    """The master token-optimized blueprint schema for downstream LLM vibe-coding."""

    metadata: SiteMetadata
    tokens: DesignTokens
    landmarks: list[SectionBlueprint] = Field(default_factory=list)
    external_production_assets: list[ExternalProductionAsset] = Field(default_factory=list)
    sophisticated_features: SophisticatedFeaturesSummary | None = Field(
        default=None,
        description="Inventory of advanced animations, sticky scroll tracks, offscreen drawers, and interactive systems requiring independent exploration",
    )
    screenshot_path: str | None = Field(
        default=None,
        description="Relative or absolute path to the captured full-page screenshot",
    )


class ComponentProp(BaseModel):
    """Inferred property or parameter for a synthesized component."""

    name: str = Field(description="Property name (e.g. 'title', 'items', 'variant')")
    prop_type: str = Field(
        description="Inferred TypeScript type (e.g. 'string', 'string[]', 'boolean', 'ReactNode')",
    )
    required: bool = Field(default=True, description="Whether this prop is universally required")
    description: str = Field(default="", description="Functional description of the prop")


class SiteComponent(BaseModel):
    """Synthesized reusable component discovered across one or more pages."""

    name: str = Field(
        description="Component name in PascalCase (e.g. 'GlobalNavbar', 'FeatureCard', 'PricingTable')",
    )
    category: Literal[
        "navigation",
        "footer",
        "card",
        "hero",
        "section",
        "disclosure",
        "modal",
        "form",
        "carousel",
        "banner",
        "unknown",
    ] = Field(default="unknown", description="Semantic component archetype")
    description: str = Field(description="Architectural description and purpose")
    occurrences: int = Field(
        default=1,
        description="Total number of times this component appears across crawled pages",
    )
    pages_found: list[str] = Field(
        default_factory=list,
        description="List of page URLs or paths where this component is used",
    )
    suggested_props: list[ComponentProp] = Field(
        default_factory=list,
        description="Inferred TypeScript props interface",
    )
    layout_hint: str | None = Field(
        default=None,
        description="CSS layout pattern (e.g. 'grid(3 cols)', 'flex-row')",
    )
    sample_content: dict[str, Any] = Field(
        default_factory=dict,
        description="Concrete sample data extracted from instances",
    )
    subcomponents: list[str] = Field(
        default_factory=list,
        description="Child component names if compound (e.g. ['AccordionTrigger', 'AccordionContent'])",
    )


class CrawledPageSummary(BaseModel):
    """Summary of an individual page within a multi-page crawl."""

    url: str = Field(description="URL of the crawled page")
    slug: str = Field(description="Filesystem-safe identifier/slug for the page")
    path: str = Field(description="URL pathname (e.g. '/', '/pricing')")
    title: str = Field(default="", description="Resolved page title")
    status_code: int = Field(default=200, description="HTTP response status code")
    landmark_count: int = Field(
        default=0,
        description="Number of primary landmark sections extracted",
    )
    pattern_count: int = Field(
        default=0,
        description="Number of repetitive pattern instances found",
    )
    features_detected: list[str] = Field(
        default_factory=list,
        description="Names of sophisticated features detected on this page",
    )
    screenshot_path: str | None = Field(
        default=None,
        description="Path to captured screenshot for this page",
    )
    blueprint_path: str = Field(description="Path to detailed page blueprint artifact")


class SiteDesignSystem(BaseModel):
    """Consolidated cross-page design system and component evidence manifest."""

    entry_url: str = Field(description="Root/starting URL for the crawl")
    domain: str = Field(description="Target site domain/hostname")
    total_pages_crawled: int = Field(
        description="Total number of successfully crawled pages",
    )
    tokens: DesignTokens = Field(
        description="Consolidated global design tokens aggregated across all pages",
    )
    global_components: list[SiteComponent] = Field(
        default_factory=list,
        description="Shared layout shells found across the entire site (e.g. GlobalNavbar, GlobalFooter)",
    )
    reusable_components: list[SiteComponent] = Field(
        default_factory=list,
        description="Modular reusable UI components cataloged from patterns across multiple pages",
    )
    motion_and_runtimes: list[str] = Field(
        default_factory=list,
        description="Consolidated motion runtimes, physics, and libraries detected site-wide",
    )
    sophisticated_features: list[SophisticatedFeature] = Field(
        default_factory=list,
        description="Deduplicated inventory of interactive systems, sticky tracks, and drawers found across the site",
    )
    external_assets: list[ExternalProductionAsset] = Field(
        default_factory=list,
        description="Deduplicated external 3D models, motion files, videos, and canvases across all pages",
    )
    pages: list[CrawledPageSummary] = Field(
        default_factory=list,
        description="Summary index of all crawled pages",
    )


class FailedPage(BaseModel):
    """A page that could not be extracted during a crawl."""

    url: str
    error: str = Field(description="Short, credential-free error summary")


class MultiPageCrawlResult(BaseModel):
    """Complete multi-page crawl result combining the unified design system and individual page blueprints."""

    design_system: SiteDesignSystem
    pages: dict[str, SiteBlueprint] = Field(
        default_factory=dict,
        description="Mapping of page URL to complete SiteBlueprint",
    )
    crawl_timestamp: str = Field(description="ISO 8601 execution timestamp")
    failed_pages: list[FailedPage] = Field(
        default_factory=list,
        description="Pages that failed extraction, with short error summaries",
    )
