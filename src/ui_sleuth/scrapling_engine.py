"""Scrapling engine pipeline: browser navigation, network listener hooks, and extraction."""

from __future__ import annotations

import datetime
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scrapling import DynamicFetcher, StealthyFetcher

from ui_sleuth.asset_inspector import (
    NetworkAssetCollector,
    inspect_canvas_and_video,
    synthesize_production_assets,
)
from ui_sleuth.dom_pruner import prune_and_map_dom
from ui_sleuth.feature_detector import detect_and_explore_features
from ui_sleuth.models import (
    DesignTokens,
    SiteBlueprint,
    SiteMetadata,
)
from ui_sleuth.proxy_manager import ProxyConfig, resolve_proxy_config
from ui_sleuth.token_extractor import harvest_design_tokens

if TYPE_CHECKING:
    from playwright.sync_api import Page

logger = logging.getLogger(__name__)


def parse_viewport(viewport_str: str) -> dict[str, int]:
    """Parse 'WIDTHxHEIGHT' string into dict {'width': int, 'height': int}."""
    parts = viewport_str.lower().split("x")
    if len(parts) == 2:
        try:
            return {"width": int(parts[0].strip()), "height": int(parts[1].strip())}
        except ValueError:
            pass
    logger.warning("Invalid viewport format '%s', falling back to 1440x900", viewport_str)
    return {"width": 1440, "height": 900}


def is_chrome_installed() -> bool:
    """Check if real Chrome/Chromium executable is available on the system."""
    return bool(shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chrome"))


# Client-side JavaScript routine to force hydration of lazy images, scroll triggers, and web fonts
HYDRATE_PAGE_JS = """
async () => {
    // 1. Force eager loading and restore data-src on all images
    document.querySelectorAll('img').forEach(img => {
        if (img.loading === 'lazy') img.loading = 'eager';
        if (img.dataset.src && !img.src) img.src = img.dataset.src;
        if (img.dataset.srcset && !img.srcset) img.srcset = img.dataset.srcset;
    });

    // 2. Progressive scroll down to trigger IntersectionObserver, lazy loading, and animations
    const getScrollHeight = () => Math.max(
        document.body.scrollHeight,
        document.documentElement.scrollHeight,
        document.body.offsetHeight,
        document.documentElement.offsetHeight
    );

    const viewportHeight = window.innerHeight || 900;
    const step = Math.max(Math.floor(viewportHeight * 0.75), 500);
    let currentY = 0;
    let maxScroll = getScrollHeight();

    while (currentY < maxScroll) {
        currentY = Math.min(currentY + step, maxScroll);
        window.scrollTo(0, currentY);
        if (window.ScrollTrigger) window.ScrollTrigger.update();
        if (window.lenis && typeof window.lenis.scrollTo === 'function') {
            try { window.lenis.scrollTo(currentY, { immediate: true }); } catch (e) {}
        }
        await new Promise(r => setTimeout(r, 40));
        maxScroll = getScrollHeight();
    }

    // 3. Advance all GSAP ScrollTrigger animations to progress 1 (fully revealed)
    if (window.ScrollTrigger && window.ScrollTrigger.getAll) {
        try {
            window.ScrollTrigger.getAll().forEach(st => {
                if (st.animation && typeof st.animation.progress === 'function') {
                    try { st.animation.progress(1); } catch (e) {}
                }
            });
        } catch (e) {}
    }

    // 4. Force image decoding
    const imgs = Array.from(document.querySelectorAll('img'));
    await Promise.all(
        imgs.map(img => (img.decode ? img.decode().catch(() => {}) : Promise.resolve()))
    );

    // 5. Ensure web fonts are completely ready
    if (document.fonts && document.fonts.ready) {
        try { await document.fonts.ready; } catch (e) {}
    }

    // 6. Dispatch synthetic scroll and resize events
    window.dispatchEvent(new Event('scroll'));
    window.dispatchEvent(new Event('resize'));
}
"""


class ScraplingEngine:
    """Core extraction engine integrating Scrapling, network hooks, and multimodal capture."""

    def __init__(
        self,
        url: str,
        wait_until: str = "networkidle",
        timeout: int = 30,
        viewport: str = "1440x900",
        proxy_config: ProxyConfig | None = None,
        use_stealth: bool = False,
        real_chrome: bool | None = None,
        headless: bool = True,
        screenshot_path: Path | None = None,
        delay: float = 2.0,
        auto_scroll: bool = True,
        explore_features: bool = True,
    ) -> None:
        self.url = url
        self.wait_until = wait_until.strip()
        self.timeout_seconds = timeout
        self.timeout_ms = timeout * 1000
        self.viewport = parse_viewport(viewport)
        self.proxy_config = proxy_config or resolve_proxy_config()
        self.use_stealth = use_stealth
        self.headless = headless
        self.screenshot_path = screenshot_path
        self.delay = max(0.0, delay)
        self.auto_scroll = auto_scroll
        self.explore_features = explore_features

        # If real_chrome is None, auto-enable if installed
        if real_chrome is None:
            self.real_chrome = is_chrome_installed()
        else:
            self.real_chrome = real_chrome

        self.collector = NetworkAssetCollector()
        self._action_data: dict[str, Any] = {}

    def _page_setup(self, page: Page) -> None:
        """Hook into Playwright page session prior to navigation to attach network listeners."""
        try:
            # Attach network interception listener for runtime 3D/video/motion assets
            page.on("response", self.collector.handle_response)

            # Intercept canvas getContext to track active context type non-destructively
            try:
                page.add_init_script("""
                (() => {
                    try {
                        const origGetContext = HTMLCanvasElement.prototype.getContext;
                        HTMLCanvasElement.prototype.getContext = function(type, ...args) {
                            if (!this.__sleuth_context_type__) {
                                this.__sleuth_context_type__ = type;
                            }
                            return origGetContext.call(this, type, ...args);
                        };
                    } catch (e) {}
                })();
                """)
            except Exception as e:
                logger.debug("Could not attach canvas init script: %s", e)

            # Set viewport dimensions
            page.set_viewport_size(
                {"width": self.viewport["width"], "height": self.viewport["height"]}
            )
            logger.debug(
                "Attached network listener and set viewport to %dx%d",
                self.viewport["width"],
                self.viewport["height"],
            )
        except Exception as e:
            logger.warning("Error during page_setup hook: %s", e)

    def _page_action(self, page: Page) -> None:
        """Execute post-navigation evaluations and capture screenshot while session is alive."""
        try:
            # 1. Progressive auto-scroll hydration for lazy images, fonts, and scroll triggers
            if self.auto_scroll:
                try:
                    logger.debug("Executing progressive auto-scroll page hydration...")
                    page.evaluate(HYDRATE_PAGE_JS)
                except Exception as e:
                    logger.debug("Page hydration evaluation encountered error: %s", e)

            # 2. Configurable post-load delay for animation stabilization
            if self.delay > 0:
                page.wait_for_timeout(int(self.delay * 1000))

            # 3. Harvest design tokens (now sampled on fully hydrated elements)
            tokens: DesignTokens = harvest_design_tokens(page)
            self._action_data["tokens"] = tokens

            # 4. Probe canvas runtime engines and video playback diagnostics
            dom_probe = inspect_canvas_and_video(page)
            self._action_data["dom_probe"] = dom_probe

            # 5. Probe and explore sophisticated interactive features
            features_summary = detect_and_explore_features(page, explore=self.explore_features)
            self._action_data["sophisticated_features"] = features_summary

            # 6. Page title and meta description
            title = page.title() or ""
            meta_desc = None
            desc_handle = page.query_selector('meta[name="description"]')
            if desc_handle:
                meta_desc = desc_handle.get_attribute("content")
            self._action_data["title"] = title
            self._action_data["description"] = meta_desc

            # 7. Capture full-page screenshot
            if self.screenshot_path:
                self.screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(self.screenshot_path), full_page=True)
                self._action_data["screenshot_saved"] = str(self.screenshot_path)
                logger.info("Saved full-page screenshot to %s", self.screenshot_path)

        except Exception as e:
            logger.warning("Error during page_action hook: %s", e)

    def execute(self) -> SiteBlueprint:
        """Execute extraction pipeline and produce validated SiteBlueprint."""
        logger.info(
            "Starting extraction for %s (wait_until=%s, timeout=%ds, stealth=%s, proxy=%s)",
            self.url,
            self.wait_until,
            self.timeout_seconds,
            self.use_stealth,
            self.proxy_config.get_display_summary() or "Direct",
        )

        fetcher_cls = StealthyFetcher if self.use_stealth else DynamicFetcher

        # Configure wait condition parameters
        fetch_kwargs: dict[str, Any] = {
            "timeout": self.timeout_ms,
            "headless": self.headless,
            "real_chrome": self.real_chrome,
            "page_setup": self._page_setup,
            "page_action": self._page_action,
            "additional_args": {
                "viewport": self.viewport,
                "device_scale_factor": 1,
            },
        }

        # Wait condition resolution
        wait_lower = self.wait_until.lower()
        if wait_lower == "networkidle":
            fetch_kwargs["network_idle"] = True
            fetch_kwargs["load_dom"] = True
        elif wait_lower == "load":
            fetch_kwargs["network_idle"] = False
            fetch_kwargs["load_dom"] = True
        else:
            # Custom CSS selector to wait for (preserves exact case)
            fetch_kwargs["wait_selector"] = self.wait_until
            fetch_kwargs["load_dom"] = True

        # Proxy integration (supports single Decodo proxy or Decodo round-robin rotator)
        # Skip proxies for loopback / local addresses
        is_loopback = "127.0.0.1" in self.url or "localhost" in self.url
        if not is_loopback:
            proxy_kwargs = self.proxy_config.to_scrapling_kwargs()
            fetch_kwargs.update(proxy_kwargs)

        # Execute Scrapling fetch
        try:
            response = fetcher_cls.fetch(self.url, **fetch_kwargs)
            status_code = getattr(response, "status", 200)
            raw_body = getattr(response, "body", b"")
            html_text = (
                raw_body.decode("utf-8", errors="replace")
                if isinstance(raw_body, bytes)
                else str(raw_body)
            )
            self._action_data["raw_html"] = html_text
        except Exception as e:
            logger.error("Scrapling fetch failed for %s: %s", self.url, e)
            raise

        # 1. Structural DOM Pruning & Repetitive Pattern Recognition
        landmarks = prune_and_map_dom(html_text)

        # 2. Design Tokens
        tokens: DesignTokens = self._action_data.get("tokens") or DesignTokens()

        # 3. Synthesize External Production Assets
        dom_probe: dict[str, Any] = self._action_data.get("dom_probe") or {}
        production_assets = synthesize_production_assets(self.collector, dom_probe)

        # 4. Assemble Metadata
        now_iso = datetime.datetime.now(datetime.UTC).isoformat()
        metadata = SiteMetadata(
            url=self.url,
            title=self._action_data.get("title", ""),
            description=self._action_data.get("description"),
            viewport=self.viewport,
            timestamp=now_iso,
            status_code=status_code,
            wait_condition_used=self.wait_until,
            proxy_used=self.proxy_config.get_display_summary(),
        )

        return SiteBlueprint(
            metadata=metadata,
            tokens=tokens,
            landmarks=landmarks,
            external_production_assets=production_assets,
            sophisticated_features=self._action_data.get("sophisticated_features"),
            screenshot_path=self._action_data.get("screenshot_saved"),
        )
