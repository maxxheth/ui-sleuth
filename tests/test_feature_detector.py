"""Tests for feature_detector module."""

from unittest.mock import MagicMock

from ui_sleuth.exporter import generate_llm_markdown
from ui_sleuth.feature_detector import MAX_FEATURES_PER_CATEGORY, detect_and_explore_features
from ui_sleuth.models import (
    DesignTokens,
    SiteBlueprint,
    SiteMetadata,
    SophisticatedFeature,
    SophisticatedFeaturesSummary,
)


def test_feature_detector_mocked_page():
    mock_page = MagicMock()
    mock_page.evaluate.return_value = {
        "detected_engines": ["GSAP (GreenSock) [ScrollTrigger, SplitText]", "Lenis Smooth Scroll"],
        "sticky_tracks": [
            {
                "name": "Sticky Track (section.home-testi)",
                "selector": "section.home-testi",
                "dimensions": {"top": 10700, "left": 50, "width": 1200, "height": 2880},
                "stickyHeight": 900,
                "childItemsCount": 4,
                "description": "Pinned scroll scene of height 2880px with sticky viewport container.",
            }
        ],
        "offscreen_drawers": [
            {
                "name": "Modal/Drawer (div.client-news)",
                "selector": "div.client-news",
                "dimensions": {"top": 7800, "left": 0, "width": 1200, "height": 8400},
                "headings": ["Outcome", "Featured", "Deets"],
                "textPreview": "Our collaboration helped Surge grow...",
                "description": "Off-screen or dynamic drawer/modal.",
            }
        ],
        "disclosures": [
            {
                "name": "Interactive Disclosure (div.home-ser-item)",
                "selector": "div.home-ser-item",
                "title": "Product Design",
                "dimensions": {"top": 2400, "left": 50, "width": 1200, "height": 400},
                "description": "Expandable disclosure.",
            }
        ],
        "marquees": [
            {
                "name": "Continuous Stream (div.home-client-list)",
                "selector": "div.home-client-list",
                "dimensions": {"top": 13800, "left": 50, "width": 1200, "height": 100},
                "description": "Infinite marquee.",
            }
        ],
        "canvases": [
            {
                "name": "WebGL Shader Canvas",
                "selector": "canvas",
                "dimensions": {"top": 200, "left": 50, "width": 800, "height": 600},
                "isWebGL": True,
                "description": "High-performance WebGL 3D canvas.",
            }
        ],
    }

    summary = detect_and_explore_features(mock_page, explore=True)
    assert summary.has_sophisticated_features is True
    assert "GSAP (GreenSock) [ScrollTrigger, SplitText]" in summary.detected_engines
    assert "Lenis Smooth Scroll" in summary.detected_engines
    assert len(summary.features) == 6
    assert len(summary.recommended_explorations) >= 3

    # Check categories
    categories = {f.category for f in summary.features}
    assert "motion_physics_engine" in categories
    assert "sticky_scroll_track" in categories
    assert "offscreen_drawer" in categories
    assert "interactive_disclosure" in categories
    assert "marquee_stream" in categories
    assert "webgl_canvas" in categories


def test_sophisticated_features_in_markdown():
    summary = SophisticatedFeaturesSummary(
        has_sophisticated_features=True,
        detected_engines=["GSAP ScrollTrigger", "Lenis"],
        features=[
            SophisticatedFeature(
                category="sticky_scroll_track",
                name="Sticky Track (section.home-testi)",
                selector="section.home-testi",
                description="Pinned scene spanning 2880px scroll track.",
                dimensions={"top": 10700, "height": 2880},
                requires_independent_exploration=True,
                suggested_implementation="Use Framer Motion useScroll().",
            )
        ],
        recommended_explorations=["Preserve sticky phase-switching for section.home-testi."],
    )

    blueprint = SiteBlueprint(
        metadata=SiteMetadata(
            url="https://test.com",
            timestamp="2026-10-04T12:00:00Z",
        ),
        tokens=DesignTokens(),
        sophisticated_features=summary,
    )

    md = generate_llm_markdown(blueprint)
    assert "## 4. SOPHISTICATED INTERACTIVE FEATURES & MOTION MANIFEST" in md
    assert "`GSAP ScrollTrigger`, `Lenis`" in md
    assert "`section.home-testi`" in md
    blocks = [
        part.split("\n```", 1)[0] for part in md.split("\n```json untrusted-page-content\n")[1:]
    ]
    assert any("Sticky Track (section.home-testi)" in b for b in blocks)
    outside = md
    for b in blocks:
        outside = outside.replace(b, "")
    assert "Sticky Track (section.home-testi)" not in outside
    assert "Preserve sticky phase-switching" in md
    assert "## 5. SEMANTIC COMPONENT ARCHITECTURE & LANDMARKS" in md
    assert "## 6. RECONSTRUCTION IMPLEMENTATION INSTRUCTIONS" in md


def test_kinetic_micro_interactions_detection():
    mock_page = MagicMock()
    mock_page.evaluate.return_value = {
        "detected_engines": ["GSAP (GreenSock)", "Matter.js (2D Physics)"],
        "sticky_tracks": [],
        "offscreen_drawers": [],
        "disclosures": [],
        "marquees": [],
        "canvases": [],
        "magnetic_elements": [
            {
                "selector": "a.btn[data-magnetic]",
                "count": 5,
                "strength": "25",
                "sample_labels": ["Play Reel", "Book Call"],
                "dimensions": {"top": 100, "left": 50, "width": 120, "height": 48},
                "description": "Kinetic magnetic cursor physics applied to 5 interactive elements.",
            }
        ],
        "custom_cursors": [
            {
                "selector": "div.cursor-wrap",
                "dimensions": {"top": 0, "left": 0, "width": 24, "height": 24},
                "states": ["btn", "txtLink"],
                "has_image_preview": True,
                "preview_count": 8,
                "sample_preview_images": [
                    {
                        "imgUrl": "https://example.com/thumb.jpg",
                        "ratio": "300/168",
                        "label": "Project A",
                    }
                ],
                "description": "Interactive cursor follower supporting 2 dynamic states.",
            }
        ],
        "typographic_reveals": [
            {
                "selector": "h1.home-hero-title",
                "split_type": "characters",
                "text_preview": "Crafting digital experiences",
                "count": 1,
                "dimensions": {"top": 200, "left": 50, "width": 800, "height": 100},
                "description": "SplitText cascade reveal on headline.",
            }
        ],
        "hover_media_switchers": [
            {
                "trigger_selector": "li.client-row[data-video]",
                "target_selector": ".hero-visual",
                "media_samples": ["https://example.com/reel1.mp4"],
                "count": 4,
                "dimensions": {"top": 500, "left": 50, "width": 400, "height": 60},
                "description": "Dynamic media switcher.",
            }
        ],
        "scroll_parallaxes": [
            {
                "selector": "div.floating-card[data-move]",
                "move_mode": "inner, wrap",
                "count": 3,
                "dimensions": {"top": 800, "left": 50, "width": 300, "height": 200},
                "description": "Scroll-driven parallax motion on 3 elements.",
            }
        ],
    }

    summary = detect_and_explore_features(mock_page, explore=True)
    assert summary.has_sophisticated_features is True
    assert "Matter.js (2D Physics)" in summary.detected_engines

    cat_map = {f.category: f for f in summary.features}
    assert "magnetic_physics" in cat_map
    assert "custom_cursor" in cat_map
    assert "typographic_reveal" in cat_map
    assert "hover_media_switcher" in cat_map
    assert "scroll_parallax" in cat_map

    assert cat_map["magnetic_physics"].details["strength"] == "25"
    assert cat_map["custom_cursor"].details["has_image_preview"] is True
    assert cat_map["typographic_reveal"].details["split_type"] == "characters"

    blueprint = SiteBlueprint(
        metadata=SiteMetadata(
            url="https://konpo-test.com",
            timestamp="2026-10-05T12:00:00Z",
        ),
        tokens=DesignTokens(),
        sophisticated_features=summary,
    )

    md = generate_llm_markdown(blueprint)
    assert "Kinetic Micro-Interactions & Cursor Physics Runbook" in md
    assert "Magnetic Button Physics" in md
    assert "Interactive Custom Cursor & Floating Card Preview" in md
    assert "Typographic SplitText Cascade" in md
    assert "Dynamic Hover Media Switcher" in md


def test_malformed_and_nan_probe_entries_do_not_raise():
    from unittest.mock import MagicMock

    from ui_sleuth.feature_detector import detect_and_explore_features

    nan, inf = float("nan"), float("inf")
    probe = {
        "detected_engines": ["gsap", 5, None],
        "sticky_tracks": [
            None,
            "junk",
            {"description": "no selector"},
            {"selector": ".ok", "dimensions": {"height": nan, "width": 100.7, "top": inf}},
            {"selector": ".bad-dims", "dimensions": "oops", "stickyHeight": nan},
        ],
        "offscreen_drawers": {"not": "a list"},
        "marquees": [{"selector": ".m", "name": 3, "dimensions": {"height": -inf}}],
        "scroll_parallaxes": [{"selector": ".p", "dimensions": {"x": True}}],
        "hover_media_switchers": [{"target_selector": ".t"}],
    }
    page = MagicMock()
    page.evaluate.return_value = probe
    summary = detect_and_explore_features(page)

    by_selector = {f.selector: f for f in summary.features}
    assert set(by_selector) == {"window", ".ok", ".bad-dims", ".m", ".p"}
    assert by_selector["window"].details == {"engines": ["gsap"]}
    assert by_selector[".ok"].dimensions == {"width": 100}
    assert by_selector[".bad-dims"].dimensions == {}
    assert by_selector[".bad-dims"].details["stickyHeight"] is None
    assert by_selector[".m"].dimensions == {}
    assert by_selector[".p"].dimensions == {}


def test_non_dict_probe_result_returns_empty_summary():
    from unittest.mock import MagicMock

    from ui_sleuth.feature_detector import detect_and_explore_features

    page = MagicMock()
    page.evaluate.return_value = None
    assert detect_and_explore_features(page).features == []


def test_no_feature_reports_explored_and_explore_flag_is_noop():
    probe = {
        "detected_engines": ["GSAP (GreenSock)"],
        "sticky_tracks": [{"selector": ".s", "dimensions": {"height": 2000}}],
        "offscreen_drawers": [{"selector": ".d"}],
        "disclosures": [{"selector": ".a"}],
        "marquees": [{"selector": ".m"}],
        "canvases": [{"selector": "canvas", "name": "2D Dynamic Canvas"}],
        "magnetic_elements": [{"selector": "[data-magnetic]"}],
        "custom_cursors": [{"selector": "body"}],
        "typographic_reveals": [{"selector": "h1"}],
        "hover_media_switchers": [{"trigger_selector": "[data-video]"}],
        "scroll_parallaxes": [{"selector": "[data-move]"}],
    }
    for explore in (True, False):
        page = MagicMock()
        page.evaluate.return_value = probe
        summary = detect_and_explore_features(page, explore=explore)
        assert len(summary.features) == 11
        assert {f.exploration_status for f in summary.features} == {"detected"}


def test_nested_marquee_nodes_are_capped_with_summary():
    marquees = [{"selector": f"div.marquee-{i % 8}", "dimensions": {"top": i}} for i in range(50)]
    page = MagicMock()
    page.evaluate.return_value = {"marquees": marquees}
    summary = detect_and_explore_features(page)
    mq = [f for f in summary.features if f.category == "marquee_stream"]
    assert len(mq) == MAX_FEATURES_PER_CATEGORY + 1  # cap + one "+N more" summary
    kept, overflow = mq[:-1], mq[-1]
    assert overflow.details["omitted"] == sum(1 for i in range(50) if i % 8 >= 5)
    assert overflow.name.startswith("+")
    assert sum(f.details["count"] for f in kept) + overflow.details["omitted"] == 50


def test_same_selector_marquees_merge_into_one_with_count():
    page = MagicMock()
    page.evaluate.return_value = {"marquees": [{"selector": "div.m"} for _ in range(50)]}
    summary = detect_and_explore_features(page)
    assert len(summary.features) == 1
    assert summary.features[0].details["count"] == 50


def test_runbook_uses_detected_selectors_not_reference_site_ones():
    page = MagicMock()
    page.evaluate.return_value = {
        "custom_cursors": [{"selector": "body", "element_detected": False}],
        "hover_media_switchers": [{"trigger_selector": "li.row[data-video]"}],
        "magnetic_elements": [{"selector": "a.cta[data-magnetic]"}],
        "typographic_reveals": [{"selector": "h2.title"}],
    }
    summary = detect_and_explore_features(page)
    md = generate_llm_markdown(
        SiteBlueprint(
            metadata=SiteMetadata(url="https://x.test", timestamp="2026-01-01T00:00:00Z"),
            tokens=DesignTokens(),
            sophisticated_features=summary,
        )
    )
    for hardcoded in (".cursor-wrap", ".cursor-img", "#hero-active-video", ".hero-visual"):
        assert hardcoded not in md
    assert '"li.row[data-video]"' in md
    assert '"a.cta[data-magnetic]"' in md
    assert '"h2.title"' in md
    assert "not detected: pass your preview" in md
    # Detected target selector is used when reported
    page.evaluate.return_value = {
        "hover_media_switchers": [{"trigger_selector": "li.row", "target_selector": "#preview"}],
    }
    summary = detect_and_explore_features(page)
    assert summary.features[0].details["target_selector"] == "#preview"
