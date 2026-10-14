"""Specialized asset and runtime inspector for 3D, vector motion, and rich video pipelines."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from ui_sleuth.models import ExternalProductionAsset

if TYPE_CHECKING:
    from playwright.sync_api import Page
    from playwright.sync_api import Response as PlaywrightResponse

logger = logging.getLogger(__name__)

# File extension patterns
PATTERN_3D = re.compile(r"\.(gltf|glb|splinecode|usdz|fbx|bin|obj)($|\?)", re.IGNORECASE)
PATTERN_MOTION = re.compile(r"\.(riv|lottie)($|\?)", re.IGNORECASE)
PATTERN_VIDEO = re.compile(r"\.(mp4|webm|m3u8|mpd|mov)($|\?)", re.IGNORECASE)
PATTERN_LOTTIE_JSON = re.compile(r"\.json($|\?)", re.IGNORECASE)

# Client-side JavaScript evaluation for canvas and video diagnostics
CANVAS_VIDEO_PROBE_JS = """
() => {
    // 1. Evaluate runtime engines mounted on window
    const detectedEngines = [];
    if (typeof window.THREE !== 'undefined' || window.__THREE__) detectedEngines.push('Three.js');
    if (typeof window.BABYLON !== 'undefined') detectedEngines.push('Babylon.js');
    if (typeof window.spline !== 'undefined' || typeof window.SplineRuntime !== 'undefined' || document.querySelector('spline-viewer')) detectedEngines.push('Spline');
    if (typeof window.rive !== 'undefined' || typeof window.Rive !== 'undefined' || document.querySelector('rive-canvas')) detectedEngines.push('Rive');
    if (typeof window.lottie !== 'undefined' || typeof window.bodymovin !== 'undefined' || document.querySelector('lottie-player, dotlottie-player')) detectedEngines.push('Lottie');
    if (typeof window.PIXI !== 'undefined') detectedEngines.push('PixiJS');

    function getLandmark(el) {
        const lm = el.closest('header, nav, main, section, article, aside, footer');
        if (lm) {
            let desc = lm.tagName.toLowerCase();
            if (lm.id) desc += '#' + lm.id;
            return desc;
        }
        return 'unknown';
    }

    // 2. Evaluate Canvas nodes
    const canvasList = [];
    const canvases = document.querySelectorAll('canvas');
    canvases.forEach((c, idx) => {
        let contextType = c.__sleuth_context_type__ || 'unknown';
        // Test context creation or existing context safely
        if (contextType === 'unknown') {
            try {
                if (c.getContext('2d')) {
                    contextType = '2d';
                } else if (c.getContext('webgl2')) {
                    contextType = 'webgl2';
                } else if (c.getContext('webgl') || c.getContext('experimental-webgl')) {
                    contextType = 'webgl';
                }
            } catch (e) {}
        }

        canvasList.push({
            index: idx,
            id: c.id || null,
            classes: c.className || null,
            width: c.width,
            height: c.height,
            context_type: contextType,
            parent_landmark: getLandmark(c),
            aria_label: c.getAttribute('aria-label') || null
        });
    });

    // 3. Evaluate Video nodes
    const videoList = [];
    const videos = document.querySelectorAll('video');
    videos.forEach((v, idx) => {
        const autoplay = v.autoplay || v.hasAttribute('autoplay');
        const muted = v.muted || v.hasAttribute('muted');
        const loop = v.loop || v.hasAttribute('loop');
        const playsinline = v.playsInline || v.hasAttribute('playsinline');
        const controls = v.controls || v.hasAttribute('controls');

        const sources = [];
        if (v.src) sources.push(v.src);
        if (v.currentSrc && !sources.includes(v.currentSrc)) sources.push(v.currentSrc);
        v.querySelectorAll('source').forEach(s => {
            if (s.src && !sources.includes(s.src)) sources.push(s.src);
        });

        const isBackgroundLoop = Boolean(autoplay && muted && loop);

        videoList.push({
            index: idx,
            id: v.id || null,
            parent_landmark: getLandmark(v),
            sources: sources,
            poster: v.poster || null,
            autoplay: autoplay,
            muted: muted,
            loop: loop,
            playsinline: playsinline,
            controls: controls,
            is_background_loop: isBackgroundLoop
        });
    });

    return {
        detected_window_engines: detectedEngines,
        canvases: canvasList,
        videos: videoList
    };
}
"""


class NetworkAssetCollector:
    """Intersects and logs network responses during page load to discover binary/runtime assets."""

    def __init__(self) -> None:
        self.assets_3d: list[str] = []
        self.assets_motion: list[str] = []
        self.assets_video: list[str] = []
        self.all_responses: list[dict[str, Any]] = []

    def handle_response(self, response: PlaywrightResponse) -> None:
        """Playwright response event listener."""
        try:
            url = response.url
            status = response.status
            if status >= 400:
                return

            headers = response.headers
            content_type = headers.get("content-type", "").lower()

            self.all_responses.append(
                {
                    "url": url,
                    "status": status,
                    "content_type": content_type,
                }
            )

            # Check 3D models
            if (PATTERN_3D.search(url) or "model/gltf" in content_type) and (
                url not in self.assets_3d
            ):
                self.assets_3d.append(url)
                logger.debug("Captured 3D model asset: %s", url)

            # Check vector motion / Rive / Lottie
            elif (PATTERN_MOTION.search(url) or "application/x-rive" in content_type) and (
                url not in self.assets_motion
            ):
                self.assets_motion.append(url)
                logger.debug("Captured motion asset: %s", url)
            elif (
                PATTERN_LOTTIE_JSON.search(url)
                and ("lottie" in url.lower() or "bodymovin" in url.lower() or "anim" in url.lower())
                and (url not in self.assets_motion)
            ):
                self.assets_motion.append(url)
                logger.debug("Captured Lottie JSON asset: %s", url)

            # Check rich video loops / streams
            elif (PATTERN_VIDEO.search(url) or "video/" in content_type) and (
                url not in self.assets_video
            ):
                self.assets_video.append(url)
                logger.debug("Captured video stream asset: %s", url)

        except Exception as e:
            logger.debug("Error processing network response: %s", e)


def inspect_canvas_and_video(page: Page) -> dict[str, Any]:
    """Execute client-side JavaScript evaluation to probe canvas contexts and video elements."""
    try:
        data: Any = page.evaluate(CANVAS_VIDEO_PROBE_JS)
        if isinstance(data, dict):
            return data
    except Exception as e:
        logger.warning("Failed to evaluate canvas/video diagnostics: %s", e)

    return {"detected_window_engines": [], "canvases": [], "videos": []}


def synthesize_production_assets(
    network_collector: NetworkAssetCollector,
    dom_probe: dict[str, Any],
) -> list[ExternalProductionAsset]:
    """Synthesize network intercepted URLs with DOM canvas and video diagnostics."""
    production_assets: list[ExternalProductionAsset] = []
    window_engines: list[str] = dom_probe.get("detected_window_engines", [])
    canvases: list[dict[str, Any]] = dom_probe.get("canvases", [])
    videos: list[dict[str, Any]] = dom_probe.get("videos", [])

    # 1. 3D Model Assets
    if network_collector.assets_3d:
        has_spline = (
            any("splinecode" in u for u in network_collector.assets_3d)
            or "Spline" in window_engines
        )
        has_three = "Three.js" in window_engines or not has_spline

        engines: list[str] = []
        if has_spline:
            engines.append("Spline")
        if has_three:
            engines.append("Three.js")
        if "Babylon.js" in window_engines:
            engines.append("Babylon.js")
        if not engines:
            engines.append("Three.js (Standard WebGL GLTF Loader)")

        suggested_wrapper = (
            "@splinetool/react-spline"
            if has_spline and not has_three
            else "@react-three/fiber (@react-three/drei useGLTF)"
        )

        parent_lm = canvases[0].get("parent_landmark", "main") if canvases else "main"

        production_assets.append(
            ExternalProductionAsset(
                category="3d_model",
                detected_engines=engines,
                direct_asset_urls=list(network_collector.assets_3d),
                parent_landmark=parent_lm,
                suggested_react_wrapper=suggested_wrapper,
                details={
                    "total_models_detected": len(network_collector.assets_3d),
                    "file_types": list(
                        {
                            urlparse(u).path.split(".")[-1].lower()
                            for u in network_collector.assets_3d
                        }
                    ),
                },
            )
        )

    # 2. Vector Motion & Runtimes (Rive / Lottie)
    if network_collector.assets_motion:
        has_rive = (
            any(u.endswith(".riv") for u in network_collector.assets_motion)
            or "Rive" in window_engines
        )
        has_lottie = any("lottie" in u.lower() for u in network_collector.assets_motion) or (
            "Lottie" in window_engines
        )

        engines = []
        if has_rive:
            engines.append("Rive")
        if has_lottie:
            engines.append("Lottie")
        if not engines:
            engines.append("Vector Motion Runtime")

        wrapper = (
            "@rive-app/react-canvas (useRive)" if has_rive else "@lottiefiles/react-lottie-player"
        )
        parent_lm = canvases[0].get("parent_landmark", "section") if canvases else "section"

        production_assets.append(
            ExternalProductionAsset(
                category="vector_motion",
                detected_engines=engines,
                direct_asset_urls=list(network_collector.assets_motion),
                parent_landmark=parent_lm,
                suggested_react_wrapper=wrapper,
                details={
                    "runtimes": engines,
                },
            )
        )

    # 3. Canvas nodes with WebGL/2D without direct network files (e.g. procedural shaders or PixiJS)
    if canvases and not network_collector.assets_3d and not network_collector.assets_motion:
        for c in canvases:
            ctx_type = c.get("context_type", "2d")
            engines = list(window_engines)
            if ctx_type in ["webgl", "webgl2"] and not engines:
                engines.append("Custom WebGL Shader / Three.js")

            wrapper = (
                "@react-three/fiber"
                if "Three.js" in engines or ctx_type in ["webgl", "webgl2"]
                else "HTML5 Canvas (react useRef/useEffect hook)"
            )

            production_assets.append(
                ExternalProductionAsset(
                    category="interactive_canvas",
                    detected_engines=engines or [f"HTML5 Canvas ({ctx_type})"],
                    direct_asset_urls=[],
                    parent_landmark=c.get("parent_landmark", "unknown"),
                    suggested_react_wrapper=wrapper,
                    details=c,
                )
            )

    # 4. Rich Video Loops and Video Elements
    # Merge network video URLs and DOM video nodes
    video_urls: list[str] = list(network_collector.assets_video)
    for v in videos:
        for src in v.get("sources", []):
            if src and src not in video_urls:
                video_urls.append(src)

    if videos or video_urls:
        for v in videos:
            is_loop = v.get("is_background_loop", False)
            wrapper = (
                "Standard HTML5 video loop (<video autoPlay muted loop playsInline />)"
                if is_loop
                else "Interactive Video Player Component (e.g. video.js / custom player)"
            )

            production_assets.append(
                ExternalProductionAsset(
                    category="rich_video",
                    detected_engines=["HTML5 Video Pipeline"],
                    direct_asset_urls=v.get("sources") or video_urls,
                    parent_landmark=v.get("parent_landmark", "header"),
                    suggested_react_wrapper=wrapper,
                    details={
                        "is_background_loop": is_loop,
                        "autoplay": v.get("autoplay", False),
                        "muted": v.get("muted", False),
                        "loop": v.get("loop", False),
                        "playsinline": v.get("playsinline", False),
                        "controls": v.get("controls", False),
                        "poster": v.get("poster"),
                    },
                )
            )

        # If network captured video but no <video> tags were in DOM (e.g. background canvas rendering or JS player)
        if not videos and video_urls:
            production_assets.append(
                ExternalProductionAsset(
                    category="rich_video",
                    detected_engines=["HTML5 Video Pipeline"],
                    direct_asset_urls=video_urls,
                    parent_landmark="header",
                    suggested_react_wrapper="Standard HTML5 video loop (<video autoPlay muted loop playsInline />)",
                    details={"is_background_loop": True},
                )
            )

    return production_assets
