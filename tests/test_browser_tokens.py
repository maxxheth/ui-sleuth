"""Browser-level tests for design token harvesting (CSS custom properties, color alpha)."""

from __future__ import annotations

from typing import Any

import pytest

from ui_sleuth.token_extractor import harvest_design_tokens

pytestmark = pytest.mark.browser


def _harvest(page: Any, body: str, css: str = "") -> Any:
    page.set_content(f"<html><head><style>{css}</style></head><body>{body}</body></html>")
    return harvest_design_tokens(page)


def test_layered_root_host_and_media_custom_properties(browser_page: Any) -> None:
    css = """
    @layer theme { :root, :host { --color-brand: #ff3366; --radius-md: 8px; } }
    @media (min-width: 100px) { :root { --color-media: #00ff00; } }
    @supports (display: grid) { html { --spacing-grid: 4px; } }
    .card { --color-card: #123456; }
    """
    props = _harvest(browser_page, "<p>x</p>", css).custom_properties
    assert props["--color-brand"] == "#ff3366"
    assert props["--radius-md"] == "8px"
    assert props["--color-media"] == "#00ff00"
    assert props["--spacing-grid"] == "4px"
    assert "--color-card" not in props  # non-root selector


def test_theme_attribute_roots_do_not_clobber_default(browser_page: Any) -> None:
    css = """
    :root { --color-bg: #ffffff; }
    [data-theme="dark"] { --color-bg: #000000; --color-dark-only: #111111; }
    html[data-theme=blue], .unrelated { --color-blue: #0000ff; }
    @media (prefers-color-scheme: dark) { :root { --color-bg: #222222; } }
    """
    props = _harvest(browser_page, "<p>x</p>", css).custom_properties
    assert props["--color-bg"] == "#ffffff"
    assert props["--color-dark-only"] == "#111111"
    assert props["--color-blue"] == "#0000ff"


def test_custom_property_priority_and_inline_style(browser_page: Any) -> None:
    css = ":root { --z-index-modal: 50; --font-sans: Inter; --color-x: red; }"
    browser_page.set_content(
        f"<html style='--inline-thing: 1; --color-inline: #abcdef'><head><style>{css}</style>"
        "</head><body></body></html>"
    )
    props = harvest_design_tokens(browser_page).custom_properties
    names = list(props)
    assert props["--color-inline"] == "#abcdef"
    assert props["--inline-thing"] == "1"
    assert names.index("--color-x") < names.index("--font-sans") < names.index("--z-index-modal")


def test_cross_origin_stylesheet_falls_back_to_computed_style(browser_page: Any) -> None:
    sheet_css = ":root { --color-cdn: #abcdef; }"
    browser_page.route(
        "https://cdn.example.test/theme.css",
        lambda route: route.fulfill(
            status=200, content_type="text/css", body=sheet_css, headers={}
        ),
    )
    browser_page.route(
        "https://app.example.test/",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html",
            body='<html><head><link rel="stylesheet" href="https://cdn.example.test/theme.css">'
            "</head><body><p>x</p></body></html>",
        ),
    )
    browser_page.goto("https://app.example.test/")
    browser_page.wait_for_load_state("load")
    props = harvest_design_tokens(browser_page).custom_properties
    assert props.get("--color-cdn") == "#abcdef"


def test_alpha_is_preserved_and_invisible_filtered(browser_page: Any) -> None:
    body = (
        '<button style="background: rgba(255, 0, 0, 0.5); color: #fff">a</button>'
        '<button style="background: rgb(0 0 255 / 50%)">b</button>'
        '<button style="background: oklch(0.5 0.2 30 / 0.5)">c</button>'
        '<button style="background: rgba(0, 255, 0, 0.05)">d</button>'
    )
    tokens = _harvest(browser_page, body)
    hexes = {c.hex for c in tokens.colors.all_colors}
    assert "#ff000080" in hexes
    assert "#0000ff80" in hexes
    assert any(len(h) == 9 for h in hexes if h not in {"#ff000080", "#0000ff80"})  # oklch+alpha
    assert not any(h.startswith("#00ff00") for h in hexes)


def test_modern_syntax_black_is_not_dropped(browser_page: Any) -> None:
    body = (
        '<button style="background: oklch(0 0 0)">a</button>'
        '<button style="background: rgb(0 0 0)">b</button>'
        '<button style="background: hsl(0 0% 100%)">c</button>'
        '<button style="background: not-a-color">d</button>'
    )
    tokens = _harvest(browser_page, body)
    accents = tokens.colors.accents
    assert "#000000" in accents
    assert "#ffffff" in accents
    assert accents.count("#000000") == 1
