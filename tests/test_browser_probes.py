"""Browser-backed regression tests (bundled Chromium, ``page.set_content`` only, no network)."""

from __future__ import annotations

import time
from typing import Any

import pytest

from ui_sleuth.asset_inspector import CANVAS_VIDEO_PROBE_JS
from ui_sleuth.feature_detector import PROBE_SOPHISTICATED_FEATURES_JS
from ui_sleuth.scrapling_engine import CANVAS_INIT_SCRIPT, HYDRATE_PAGE_JS

pytestmark = pytest.mark.browser

FEED_HTML = """
<html><body style="margin:0"><div id="feed"><div style="height:2000px">start</div></div>
<script>
  window.addEventListener('scroll', () => {
    const feed = document.getElementById('feed');
    for (let i = 0; i < 5; i++) {
      const d = document.createElement('div');
      d.style.height = '400px';
      feed.appendChild(d);
    }
  });
</script></body></html>
"""

HERO_HTML = """
<html><body style="margin:0">
<section style="position:relative;height:5000px">
  <img class="hero-bg" alt=""
    src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
    style="position:absolute;top:50%;left:50%;width:1000px;height:800px;
           transform:translate(-50%,-50%)">
</section></body></html>
"""


def _load_with_init_script(page: Any, body_html: str) -> None:
    """Install the canvas init script in the live page, then add canvases.

    ``set_content`` does not navigate, so ``add_init_script`` would not run; evaluating the
    script before the canvases exist is equivalent for canvases created afterwards.
    """
    page.set_content("<html><body></body></html>")
    page.evaluate(CANVAS_INIT_SCRIPT)
    page.evaluate("html => { document.body.innerHTML = html; }", body_html)


def _drawers(page: Any) -> list[str]:
    probe = page.evaluate(PROBE_SOPHISTICATED_FEATURES_JS)
    return [d["name"] for d in probe["offscreen_drawers"]]


def test_hydrate_is_bounded_on_infinite_feed(browser_page: Any) -> None:
    browser_page.set_content(FEED_HTML)
    start = time.monotonic()
    browser_page.evaluate(HYDRATE_PAGE_JS, {"maxMs": 3000})
    assert time.monotonic() - start < 5.0
    assert browser_page.evaluate("window.scrollY") == 0


def test_hydrate_returns_to_top_without_false_drawers(browser_page: Any) -> None:
    browser_page.set_content(HERO_HTML)
    assert _drawers(browser_page) == []
    browser_page.evaluate(HYDRATE_PAGE_JS, {"maxMs": 5000})
    assert browser_page.evaluate("window.scrollY") == 0
    assert _drawers(browser_page) == []


def test_drawer_detection_ignores_elements_above_viewport(browser_page: Any) -> None:
    browser_page.set_content(HERO_HTML)
    browser_page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    assert browser_page.evaluate("window.scrollY") > 0
    assert _drawers(browser_page) == []


def test_canvas_probe_does_not_create_context(browser_page: Any) -> None:
    _load_with_init_script(browser_page, '<canvas id="c" width="100" height="100"></canvas>')

    asset_probe = browser_page.evaluate(CANVAS_VIDEO_PROBE_JS)
    assert asset_probe["canvases"][0]["context_type"] == "uninitialized"
    feature_probe = browser_page.evaluate(PROBE_SOPHISTICATED_FEATURES_JS)
    assert feature_probe["canvases"][0]["isWebGL"] is False

    # The canvas is still untouched: any context type can still be created on it.
    assert browser_page.evaluate("!!document.getElementById('c').getContext('bitmaprenderer')")


def test_canvas_probe_leaves_webgl_available(browser_page: Any) -> None:
    _load_with_init_script(browser_page, '<canvas id="c"></canvas>')
    browser_page.evaluate(CANVAS_VIDEO_PROBE_JS)
    browser_page.evaluate(PROBE_SOPHISTICATED_FEATURES_JS)
    has_gl = browser_page.evaluate(
        "(() => { const c = document.getElementById('c');"
        " return !!(c.getContext('webgl2') || c.getContext('webgl')); })()"
    )
    if not has_gl:
        pytest.skip("WebGL unavailable in this headless Chromium")


def test_canvas_with_webgl_context_is_reported(browser_page: Any) -> None:
    _load_with_init_script(browser_page, '<canvas id="c"></canvas><canvas id="d"></canvas>')
    got_gl = browser_page.evaluate(
        "!!(document.getElementById('c').getContext('webgl2')"
        " || document.getElementById('c').getContext('webgl'))"
    )
    browser_page.evaluate("document.getElementById('d').getContext('2d')")
    asset_probe = browser_page.evaluate(CANVAS_VIDEO_PROBE_JS)
    types = [c["context_type"] for c in asset_probe["canvases"]]
    assert types[1] == "2d"
    feature_probe = browser_page.evaluate(PROBE_SOPHISTICATED_FEATURES_JS)
    assert feature_probe["canvases"][1]["isWebGL"] is False
    if not got_gl:
        pytest.skip("WebGL unavailable in this headless Chromium")
    assert types[0].startswith("webgl")
    assert feature_probe["canvases"][0]["isWebGL"] is True


def test_transfer_control_to_offscreen_is_tagged(browser_page: Any) -> None:
    _load_with_init_script(browser_page, '<canvas id="c"></canvas>')
    browser_page.evaluate("document.getElementById('c').transferControlToOffscreen()")
    asset_probe = browser_page.evaluate(CANVAS_VIDEO_PROBE_JS)
    assert asset_probe["canvases"][0]["context_type"] == "offscreen"


NESTED_MARQUEE_HTML = """
<div class="logo-marquee"><div class="marquee-track">
  <div class="marquee-item">a</div><div class="marquee-item">b</div>
  <div class="marquee-item">c</div></div></div>
<div class="faq-accordion"><div class="accordion-item" aria-expanded="false">q1</div>
  <div class="accordion-item" aria-expanded="false">q2</div></div>
<div data-framer-name="Hero"><span data-magnetic>x</span><a class="magnetic-btn">y</a></div>
"""


def test_marquees_and_disclosures_are_outermost_only(browser_page: Any) -> None:
    browser_page.set_content("<html><body></body></html>")
    browser_page.evaluate("html => { document.body.innerHTML = html; }", NESTED_MARQUEE_HTML)
    probe = browser_page.evaluate(PROBE_SOPHISTICATED_FEATURES_JS)
    assert [m["selector"] for m in probe["marquees"]] == ["div.logo-marquee"]
    assert [d["selector"] for d in probe["disclosures"]] == ["div.faq-accordion"]
    assert probe["detected_engines"] == ["Framer (site builder)"]
    # Selector derives from the attribute that matched, and matches the element.
    assert probe["magnetic_elements"][0]["selector"] == "span[data-magnetic]"
    assert browser_page.query_selector(probe["magnetic_elements"][0]["selector"]) is not None
    assert all("hero-visual" not in str(v) for v in probe["hover_media_switchers"])


def test_news_overlay_requires_whole_token(browser_page: Any) -> None:
    links = "".join(f"<a href='#'>l{i}</a>" for i in range(5))
    html = (
        f"<div class='newsletter-box' style='position:fixed;display:none'>{links}</div>"
        f"<div class='news-panel' style='position:fixed;display:none'>{links}</div>"
    )
    browser_page.set_content("<html><body></body></html>")
    browser_page.evaluate("html => { document.body.innerHTML = html; }", html)
    names = _drawers(browser_page)
    assert any("news-panel" in n for n in names)
    assert not any("newsletter" in n for n in names)
