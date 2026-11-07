"""Scrapling engine pipeline: browser navigation, network listener hooks, and extraction."""

from __future__ import annotations

import datetime
import ipaddress
import logging
import os
import shutil
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from scrapling import DynamicFetcher, StealthyFetcher
from scrapling.fetchers import DynamicSession, StealthySession

from ui_sleuth.asset_inspector import (
    NetworkAssetCollector,
    inspect_canvas_and_video,
    synthesize_production_assets,
)
from ui_sleuth.dom_pruner import prune_and_map_dom
from ui_sleuth.feature_detector import detect_and_explore_features
from ui_sleuth.log_redaction import install_scrapling_log_redaction
from ui_sleuth.models import (
    DesignTokens,
    SiteBlueprint,
    SiteMetadata,
)
from ui_sleuth.proxy_manager import ProxyConfig
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
    """Check whether Google Chrome (not Chromium) is installed.

    Playwright's ``channel="chrome"`` needs Google Chrome specifically. A bare ``chrome``
    command on PATH is ambiguous (it is sometimes a Chromium alias), so it is ignored.
    """
    if shutil.which("google-chrome") or shutil.which("google-chrome-stable"):
        return True
    candidates = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    return any(path.is_file() for path in candidates)


# Init script that tags every canvas with the context type the page created (non-destructive)
CANVAS_INIT_SCRIPT = """
(() => {
    try {
        const origGetContext = HTMLCanvasElement.prototype.getContext;
        HTMLCanvasElement.prototype.getContext = function(type, ...args) {
            const ctx = origGetContext.call(this, type, ...args);
            if (ctx && !this.__sleuth_context_type__) {
                this.__sleuth_context_type__ = type;
            }
            return ctx;
        };
        const origOffscreen = HTMLCanvasElement.prototype.transferControlToOffscreen;
        if (origOffscreen) {
            HTMLCanvasElement.prototype.transferControlToOffscreen = function(...args) {
                const off = origOffscreen.apply(this, args);
                if (!this.__sleuth_context_type__) {
                    this.__sleuth_context_type__ = 'offscreen';
                }
                return off;
            };
        }
    } catch (e) {}
})();
"""


# Client-side JavaScript routine to force hydration of lazy images, scroll triggers, and web fonts
HYDRATE_PAGE_JS = """
async ({ maxMs = 15000, maxSteps = 80, maxHeight = 40000 } = {}) => {
    const deadline = performance.now() + maxMs;
    const left = () => Math.max(0, deadline - performance.now());
    const withTimeout = (p, ms) => Promise.race([p, new Promise(r => setTimeout(r, ms))]);

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
    let steps = 0;

    while (currentY < Math.min(maxScroll, maxHeight) && steps++ < maxSteps && left() > 0) {
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

    // 4. Force image decoding (bounded: stalled requests must not hang hydration)
    const imgs = Array.from(document.querySelectorAll('img'));
    await withTimeout(
        Promise.all(
            imgs.map(img => (img.decode ? img.decode().catch(() => {}) : Promise.resolve()))
        ),
        Math.min(left(), 5000)
    );

    // 5. Ensure web fonts are ready (bounded)
    if (document.fonts && document.fonts.ready) {
        try { await withTimeout(document.fonts.ready, 3000); } catch (e) {}
    }

    // 6. Return to the top so later probes and the screenshot see the page at rest
    window.scrollTo(0, 0);
    try { window.lenis?.scrollTo?.(0, { immediate: true }); } catch (e) {}
    try { window.ScrollTrigger?.update?.(); } catch (e) {}
    // rAF never fires in occluded/background windows, so bound the wait
    await withTimeout(new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))), 200);

    // 7. Dispatch synthetic scroll and resize events
    window.dispatchEvent(new Event('scroll'));
    window.dispatchEvent(new Event('resize'));
}
"""


def is_loopback_url(url: str) -> bool:
    """True if the URL's hostname is a loopback address (not a substring match)."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost" or host.endswith(".localhost")


@contextmanager
def browser_session(
    url: str,
    *,
    timeout: int = 30,
    viewport: str = "1440x900",
    proxy_config: ProxyConfig | None = None,
    use_stealth: bool = False,
    real_chrome: bool | None = None,
    headless: bool = True,
) -> Generator[Any]:
    """Open one Scrapling browser session to be reused for several pages.

    Browser-level options (headless, real_chrome, viewport, proxy) are fixed for the whole
    session; per-page options (wait condition, hooks, timeout) are passed to ``fetch``.
    The session is always closed on exit, including when the body raises.
    """
    install_scrapling_log_redaction()
    proxy_config = proxy_config or ProxyConfig()
    resolved_chrome = is_chrome_installed() if real_chrome is None else real_chrome
    session_kwargs: dict[str, Any] = {
        "timeout": timeout * 1000,
        "headless": headless,
        "real_chrome": resolved_chrome,
        "additional_args": {"viewport": parse_viewport(viewport), "device_scale_factor": 1},
    }
    if is_loopback_url(url):
        if proxy_config.has_proxy:
            logger.info("Skipping configured proxy for loopback target %s", url)
    else:
        session_kwargs.update(proxy_config.to_scrapling_kwargs())
    session_cls = StealthySession if use_stealth else DynamicSession
    with session_cls(**session_kwargs) as session:
        yield session


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
        session: Any | None = None,
    ) -> None:
        install_scrapling_log_redaction()
        # Optional shared browser session (see browser_session); None opens a one-off browser
        self.session = session
        self.final_url: str | None = None
        self.url = url
        self.wait_until = wait_until.strip()
        self.timeout_seconds = timeout
        self.timeout_ms = timeout * 1000
        self.viewport = parse_viewport(viewport)
        self.proxy_config = proxy_config or ProxyConfig()
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

    def _warn(self, step: str, exc: BaseException | str) -> None:
        """Record a non-fatal step failure (short message) for SiteMetadata.warnings."""
        if isinstance(exc, BaseException):
            detail = " ".join(f"{type(exc).__name__}: {exc}".split())
        else:
            detail = exc
        message = f"{step}: {detail[:200]}"
        logger.warning("Extraction step failed (%s)", message)
        self._action_data.setdefault("warnings", []).append(message)

    def _page_setup(self, page: Page) -> None:
        """Hook into Playwright page session prior to navigation to attach network listeners."""
        # Scrapling retries call this on a fresh page: discard state from failed attempts
        self.collector = NetworkAssetCollector()
        self._action_data = {}
        try:
            # Attach network interception listener for runtime 3D/video/motion assets
            page.on("response", self.collector.handle_response)

            # Intercept canvas getContext to track active context type non-destructively
            try:
                page.add_init_script(CANVAS_INIT_SCRIPT)
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

    def _is_selector_wait(self) -> bool:
        return self.wait_until.lower() not in ("networkidle", "load")

    def _page_action(self, page: Page) -> None:
        """Execute post-navigation evaluations and capture screenshot while session is alive.

        Every step is isolated: a failure is recorded as a warning and later steps still run.
        """
        # 0. Custom selector wait (scrapling would only wait after page_action returns)
        if self._is_selector_wait():
            try:
                page.wait_for_selector(self.wait_until, state="visible", timeout=self.timeout_ms)
            except Exception as e:
                self._warn("wait_selector", e)

        # 1. Progressive auto-scroll hydration for lazy images, fonts, and scroll triggers
        if self.auto_scroll:
            try:
                logger.debug("Executing progressive auto-scroll page hydration...")
                page.evaluate(HYDRATE_PAGE_JS, {"maxMs": min(self.timeout_ms // 2, 15000)})
            except Exception as e:
                self._warn("hydration", e)

        # 2. Configurable post-load delay for animation stabilization
        if self.delay > 0:
            try:
                page.wait_for_timeout(int(self.delay * 1000))
            except Exception as e:
                self._warn("settle_delay", e)

        # 3. Harvest design tokens (now sampled on fully hydrated elements)
        try:
            tokens: DesignTokens = harvest_design_tokens(page)
            self._action_data["tokens"] = tokens
        except Exception as e:
            self._warn("design_tokens", e)

        # 4. Probe canvas runtime engines and video playback diagnostics
        try:
            self._action_data["dom_probe"] = inspect_canvas_and_video(page)
        except Exception as e:
            self._warn("asset_probe", e)

        # 5. Probe and explore sophisticated interactive features
        try:
            self._action_data["sophisticated_features"] = detect_and_explore_features(
                page, explore=self.explore_features
            )
        except Exception as e:
            self._warn("feature_detection", e)

        # 6. Page title and meta description
        try:
            self._action_data["title"] = page.title() or ""
        except Exception as e:
            self._warn("title", e)
        try:
            desc_handle = page.query_selector('meta[name="description"]')
            if desc_handle:
                self._action_data["description"] = desc_handle.get_attribute("content")
        except Exception as e:
            self._warn("meta_description", e)

        # 7. Capture full-page screenshot
        if self.screenshot_path:
            try:
                self.screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(self.screenshot_path), full_page=True)
                self._action_data["screenshot_saved"] = str(self.screenshot_path)
                logger.info("Saved full-page screenshot to %s", self.screenshot_path)
            except Exception as e:
                self._warn("screenshot", e)

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
            "page_setup": self._page_setup,
            "page_action": self._page_action,
        }
        if self.session is None:
            # Browser-level options; a shared session already carries them
            fetch_kwargs["headless"] = self.headless
            fetch_kwargs["real_chrome"] = self.real_chrome
            fetch_kwargs["additional_args"] = {
                "viewport": self.viewport,
                "device_scale_factor": 1,
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
            # Custom CSS selector: awaited at the start of _page_action (scrapling would
            # only wait after page_action, i.e. after extraction)
            fetch_kwargs["load_dom"] = True

        # Proxy integration (supports single Decodo proxy or Decodo round-robin rotator)
        # Skip proxies for loopback / local addresses
        if self.session is not None:
            pass  # proxy is configured once on the shared session
        elif is_loopback_url(self.url):
            if self.proxy_config.has_proxy:
                logger.info("Skipping configured proxy for loopback target %s", self.url)
        else:
            fetch_kwargs.update(self.proxy_config.to_scrapling_kwargs())

        # Execute Scrapling fetch
        try:
            if self.session is not None:
                try:
                    response = self.session.fetch(self.url, **fetch_kwargs)
                finally:
                    # Pooled tabs would keep listeners/init scripts from this page
                    close_pages = getattr(self.session, "close_pages", None)
                    if callable(close_pages):
                        close_pages()
            else:
                response = fetcher_cls.fetch(self.url, **fetch_kwargs)
            final_url = getattr(response, "url", None)
            self.final_url = final_url if isinstance(final_url, str) else None
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
            warnings=list(self._action_data.get("warnings", [])),
        )

        return SiteBlueprint(
            metadata=metadata,
            tokens=tokens,
            landmarks=landmarks,
            external_production_assets=production_assets,
            sophisticated_features=self._action_data.get("sophisticated_features"),
            screenshot_path=self._action_data.get("screenshot_saved"),
        )
