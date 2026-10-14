"""Detection and isolated exploration of sophisticated UI features and motion engines."""

from __future__ import annotations

import logging
from typing import Any

from playwright.sync_api import Page

from ui_sleuth.models import SophisticatedFeature, SophisticatedFeaturesSummary

logger = logging.getLogger(__name__)

PROBE_SOPHISTICATED_FEATURES_JS = """
() => {
    const results = {
        detected_engines: [],
        sticky_tracks: [],
        offscreen_drawers: [],
        disclosures: [],
        marquees: [],
        canvases: [],
        magnetic_elements: [],
        custom_cursors: [],
        typographic_reveals: [],
        hover_media_switchers: [],
        scroll_parallaxes: []
    };

    const viewportHeight = window.innerHeight || 900;
    const viewportWidth = window.innerWidth || 1440;

    function makeElementSelector(el) {
        if (!el || !el.tagName) return 'element';
        const tag = el.tagName.toLowerCase();
        if (el.id) return `${tag}#${el.id}`;
        if (el.getAttribute && el.getAttribute('data-testid')) {
            return `${tag}[data-testid="${el.getAttribute('data-testid')}"]`;
        }
        if (typeof el.className === 'string' && el.className.trim()) {
            const classes = el.className.trim().split(/\\s+/)
                .filter(c => c && !c.includes(':') && !c.includes('/') && !c.includes('[') && !c.includes('%') && !c.includes('.'));
            if (classes.length > 0) {
                return `${tag}.${classes.slice(0, 2).join('.')}`;
            }
        }
        return tag;
    }

    // 1. Motion & Physics Engine Detection
    if (window.gsap) {
        let gsapDesc = 'GSAP (GreenSock)';
        const plugins = [];
        if (window.ScrollTrigger) plugins.push('ScrollTrigger');
        if (window.SplitText) plugins.push('SplitText');
        if (window.Flip) plugins.push('Flip');
        if (window.Draggable) plugins.push('Draggable');
        if (plugins.length > 0) gsapDesc += ` [${plugins.join(', ')}]`;
        results.detected_engines.push(gsapDesc);
    }
    if (window.lenis || document.documentElement.classList.contains('lenis') || window.Lenis) {
        results.detected_engines.push('Lenis Smooth Scroll');
    }
    if (window.LocomotiveScroll || document.querySelector('[data-scroll-container]')) {
        results.detected_engines.push('Locomotive Scroll');
    }
    if (window.THREE || window.__THREE__) {
        results.detected_engines.push('Three.js (WebGL)');
    }
    if (window.Matter || window.matter) {
        results.detected_engines.push('Matter.js (2D Physics)');
    }
    if (document.querySelector('spline-viewer, [data-spline]')) {
        results.detected_engines.push('Spline 3D Runtime');
    }
    if (document.querySelector('rive-canvas, [data-rive]')) {
        results.detected_engines.push('Rive Vector Motion');
    }
    if (window.lottie || document.querySelector('dotlottie-player, lottie-player')) {
        results.detected_engines.push('Lottie Animations');
    }
    if (document.querySelector('[data-framer-name], [data-framer-component]')) {
        results.detected_engines.push('Framer Motion');
    }

    // 2. Sticky Scroll-Jacked / Pinned Scenes Detection
    // Elements with position: sticky or GSAP ScrollTrigger pin
    const allEls = document.querySelectorAll('*');
    const seenSticky = new Set();

    for (const el of allEls) {
        const style = window.getComputedStyle(el);
        if (style.position === 'sticky') {
            const parent = el.parentElement;
            const parentRect = parent ? parent.getBoundingClientRect() : null;
            const elRect = el.getBoundingClientRect();

            // A sticky scene track typically has a parent taller than the viewport
            const trackHeight = parentRect ? Math.round(parentRect.height) : Math.round(elRect.height);
            const selector = parent ? makeElementSelector(parent) : makeElementSelector(el);
            const dedupeKey = `${selector}@${Math.round((parentRect ? parentRect.top : elRect.top) + window.scrollY)}`;

            if (!seenSticky.has(dedupeKey) && trackHeight > viewportHeight * 1.2) {
                seenSticky.add(dedupeKey);
                const itemsCount = el.querySelectorAll('[class*="item"], [class*="card"], li').length;
                results.sticky_tracks.push({
                    name: `Sticky Track (${selector})`,
                    selector: selector,
                    dimensions: {
                        top: parentRect ? Math.round(parentRect.top + window.scrollY) : 0,
                        left: Math.round(elRect.left),
                        width: Math.round(elRect.width),
                        height: trackHeight
                    },
                    stickyHeight: Math.round(elRect.height),
                    childItemsCount: itemsCount,
                    description: `Pinned scroll scene of height ${trackHeight}px with sticky viewport container (${Math.round(elRect.height)}px). Driven by progressive scrolling.`
                });
            }
        }
    }

    // Also check GSAP ScrollTrigger pinned triggers
    if (window.ScrollTrigger && window.ScrollTrigger.getAll) {
        const triggers = window.ScrollTrigger.getAll();
        for (const st of triggers) {
            if (st.pin && st.trigger) {
                const el = st.trigger;
                const selector = makeElementSelector(el);
                const r = el.getBoundingClientRect();
                const dedupeKey = `${selector}@${Math.round(r.top + window.scrollY)}`;
                if (!seenSticky.has(dedupeKey)) {
                    seenSticky.add(dedupeKey);
                    results.sticky_tracks.push({
                        name: `GSAP Pinned Scene (${selector})`,
                        selector: selector,
                        dimensions: {
                            top: Math.round(r.top + window.scrollY),
                            left: Math.round(r.left),
                            width: Math.round(r.width),
                            height: Math.round(st.end - st.start)
                        },
                        stickyHeight: Math.round(r.height),
                        childItemsCount: el.querySelectorAll('*').length,
                        description: `GSAP ScrollTrigger pinned sequence running from scroll offset ${Math.round(st.start)}px to ${Math.round(st.end)}px.`
                    });
                }
            }
        }
    }

    // 3. Off-Screen Drawers, Modals & Floating Overlays
    const seenDrawers = new Set();
    for (const el of allEls) {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        const transform = style.transform || '';
        const isOffscreenTransform = transform.includes('matrix') && (
            // check if translated outside normal bounds
            rect.top > viewportHeight * 2 || rect.left > viewportWidth || rect.right < 0 || rect.bottom < -100
        );
        const isLargeModal = (rect.height > 500 || el.scrollHeight > 500) && (
            style.position === 'fixed' || style.position === 'absolute'
        ) && (
            style.display === 'none' ||
            style.visibility === 'hidden' ||
            parseFloat(style.opacity || '1') === 0 ||
            isOffscreenTransform ||
            (el.className && typeof el.className === 'string' && (
                el.className.includes('modal') ||
                el.className.includes('drawer') ||
                el.className.includes('news') ||
                el.className.includes('popup') ||
                el.className.includes('pop-')
            ))
        );

        if (isLargeModal) {
            const selector = makeElementSelector(el);
            const dedupeKey = el.id ? selector : `${selector}@${Math.round(rect.top + window.scrollY)}`;

            if (!seenDrawers.has(dedupeKey) && selector !== 'body' && selector !== 'html') {
                seenDrawers.add(dedupeKey);
                const headings = Array.from(el.querySelectorAll('h1, h2, h3, [class*="title"]'))
                    .map(h => (h.innerText || '').trim())
                    .filter(t => t.length > 0)
                    .slice(0, 5);
                const textPreview = (el.innerText || '').trim().slice(0, 150).replace(/[\\r\\n]+/g, ' ');

                results.offscreen_drawers.push({
                    name: `Modal/Drawer (${selector})`,
                    selector: selector,
                    dimensions: {
                        top: Math.round(rect.top + window.scrollY),
                        left: Math.round(rect.left),
                        width: Math.round(rect.width || el.scrollWidth),
                        height: Math.round(rect.height || el.scrollHeight)
                    },
                    headings: headings,
                    textPreview: textPreview,
                    description: `Off-screen or dynamic drawer/modal containing ${headings.length} headings and substantial structured content.`
                });
            }
        }
    }

    // 4. Interactive Disclosures & Accordions
    const accordionTriggers = document.querySelectorAll(
        'details, [aria-expanded], [class*="accordion"], [class*="dropdown"], .home-ser-item, [data-accordion]'
    );
    const seenAccordions = new Set();
    for (const el of accordionTriggers) {
        const selector = makeElementSelector(el);
        const r = el.getBoundingClientRect();
        const dedupeKey = el.id ? selector : `${selector}@${Math.round(r.top + window.scrollY)}`;
        if (!seenAccordions.has(dedupeKey)) {
            seenAccordions.add(dedupeKey);
            const titleEl = el.querySelector('summary, [class*="title"], [class*="head"], h1, h2, h3, h4');
            const title = titleEl ? (titleEl.innerText || '').trim() : '';
            results.disclosures.push({
                name: `Interactive Disclosure (${selector})`,
                selector: selector,
                title: title,
                dimensions: {
                    top: Math.round(r.top + window.scrollY),
                    left: Math.round(r.left),
                    width: Math.round(r.width),
                    height: Math.round(r.height)
                },
                description: `Expandable disclosure / accordion element containing interactive sub-content.`
            });
        }
    }

    // 5. Marquees & Continuous Tickers
    const marquees = document.querySelectorAll('[class*="marquee"], [class*="ticker"], [class*="client-list"]');
    for (const el of marquees) {
        const selector = makeElementSelector(el);
        const r = el.getBoundingClientRect();
        results.marquees.push({
            name: `Continuous Stream (${selector})`,
            selector: selector,
            dimensions: {
                top: Math.round(r.top + window.scrollY),
                left: Math.round(r.left),
                width: Math.round(r.width),
                height: Math.round(r.height)
            },
            description: `Infinite marquee or animated ticker stream containing logo/card repetitions.`
        });
    }

    // 6. Dynamic Canvases
    const canvases = document.querySelectorAll('canvas');
    for (const c of canvases) {
        let gl = false;
        if (c.__sleuth_context_type__) {
            gl = c.__sleuth_context_type__.includes('webgl');
        } else {
            try {
                if (c.getContext('2d')) {
                    gl = false;
                } else if (c.getContext('webgl2') || c.getContext('webgl') || c.getContext('experimental-webgl')) {
                    gl = true;
                }
            } catch (e) {}
        }
        const r = c.getBoundingClientRect();
        results.canvases.push({
            name: gl ? 'WebGL Shader Canvas' : '2D Dynamic Canvas',
            selector: 'canvas',
            dimensions: {
                top: Math.round(r.top + window.scrollY),
                left: Math.round(r.left),
                width: Math.round(r.width),
                height: Math.round(r.height)
            },
            isWebGL: !!gl,
            description: gl
                ? 'High-performance WebGL 3D or shader animation canvas.'
                : '2D interactive canvas element.'
        });
    }

    // 7. Kinetic Magnetic Elements ([data-magnetic], etc.)
    const magneticEls = document.querySelectorAll('[data-magnetic], [class*="magnetic"], [data-magnetic-strength]');
    if (magneticEls.length > 0) {
        const sampleLabels = [];
        let dominantStrength = '20';
        let sampleRect = null;
        let sampleSelector = '[data-magnetic]';

        magneticEls.forEach((el, idx) => {
            const str = el.getAttribute('data-magnetic') || el.getAttribute('data-magnetic-strength') || '20';
            if (idx === 0) {
                dominantStrength = str;
                const r = el.getBoundingClientRect();
                sampleRect = {
                    top: Math.round(r.top + window.scrollY),
                    left: Math.round(r.left),
                    width: Math.round(r.width),
                    height: Math.round(r.height)
                };
                const tag = el.tagName.toLowerCase();
                const cls = typeof el.className === 'string' ? el.className.split(/\\s+/)[0] : '';
                sampleSelector = cls ? `${tag}.${cls}[data-magnetic]` : `${tag}[data-magnetic]`;
            }
            const txt = (el.innerText || el.getAttribute('aria-label') || '').trim();
            if (txt && sampleLabels.length < 5) {
                sampleLabels.push(txt.slice(0, 30));
            }
        });

        results.magnetic_elements.push({
            selector: sampleSelector,
            count: magneticEls.length,
            strength: dominantStrength,
            sample_labels: sampleLabels,
            dimensions: sampleRect || { top: 0, left: 0, width: 0, height: 0 },
            description: `Kinetic magnetic cursor physics applied to ${magneticEls.length} interactive elements (strength=${dominantStrength}). Elements pull towards cursor on hover.`
        });
    }

    // 8. Custom Interactive Cursor & Floating Image Previews
    const cursorEls = document.querySelectorAll('.cursor-wrap, .custom-cursor, #cursor, [data-cursor-wrap], [class*="cursor-inner"]');
    const cursorInteractiveEls = document.querySelectorAll('[data-cursor], [data-cursor-img], [data-cursor-text]');
    const bodyCursor = window.getComputedStyle(document.body).cursor;

    if (cursorEls.length > 0 || cursorInteractiveEls.length > 0 || bodyCursor === 'none') {
        const states = new Set();
        const previewImages = [];
        let previewCount = 0;

        cursorInteractiveEls.forEach(el => {
            const st = el.getAttribute('data-cursor');
            if (st) states.add(st);
            const img = el.getAttribute('data-cursor-img');
            if (img) {
                previewCount++;
                if (previewImages.length < 5) {
                    previewImages.push({
                        imgUrl: img,
                        ratio: el.getAttribute('data-cursor-img-ratio') || 'auto',
                        label: (el.innerText || '').trim().slice(0, 30)
                    });
                }
            }
        });

        let cursorSelector = '.cursor-wrap';
        let cursorRect = { top: 0, left: 0, width: 24, height: 24 };
        if (cursorEls.length > 0) {
            const first = cursorEls[0];
            const tag = first.tagName.toLowerCase();
            const cls = typeof first.className === 'string' ? first.className.split(/\\s+/)[0] : '';
            cursorSelector = cls ? `${tag}.${cls}` : tag;
            const r = first.getBoundingClientRect();
            cursorRect = {
                top: Math.round(r.top + window.scrollY),
                left: Math.round(r.left),
                width: Math.round(r.width),
                height: Math.round(r.height)
            };
        }

        results.custom_cursors.push({
            selector: cursorSelector,
            dimensions: cursorRect,
            states: Array.from(states),
            has_image_preview: previewCount > 0,
            preview_count: previewCount,
            sample_preview_images: previewImages,
            description: `Interactive cursor follower supporting ${states.size} dynamic state(s) (${Array.from(states).join(', ') || 'default'}) and ${previewCount} floating hover image card(s).`
        });
    }

    // 9. Typographic SplitText Reveals
    const splitCandidates = document.querySelectorAll(
        '.split-text, [data-split], .words, .chars, .lines, ' +
        '.home-hero-title, .home-abt-title, .ser-hero-title, .ser-why-item-title, .projlist-title, ' +
        'h1[class*="title"], h2[class*="title"]'
    );
    const seenSplit = new Set();
    splitCandidates.forEach(el => {
        const tag = el.tagName.toLowerCase();
        const cls = typeof el.className === 'string' ? el.className.split(/\\s+/)[0] : '';
        const selector = cls ? `${tag}.${cls}` : tag;
        if (!seenSplit.has(selector) && seenSplit.size < 6) {
            seenSplit.add(selector);
            const r = el.getBoundingClientRect();
            const txt = (el.innerText || '').trim().slice(0, 80).replace(/[\\r\\n]+/g, ' ');
            const hasNestedChars = el.querySelectorAll('.char, .chars, [class*="char"]').length > 0;
            const hasNestedWords = el.querySelectorAll('.word, .words, [class*="word"]').length > 0;
            const splitType = hasNestedChars ? 'characters' : (hasNestedWords ? 'words' : 'lines/words');

            results.typographic_reveals.push({
                selector: selector,
                split_type: splitType,
                text_preview: txt,
                count: 1,
                dimensions: {
                    top: Math.round(r.top + window.scrollY),
                    left: Math.round(r.left),
                    width: Math.round(r.width),
                    height: Math.round(r.height)
                },
                description: `SplitText cascade reveal on "${txt}" split by ${splitType} with staggered upward entrance.`
            });
        }
    });

    // 10. Hover Dynamic Media Switchers
    const mediaTriggers = document.querySelectorAll(
        '[data-video="to-play"], [data-thumb-video], [data-video-target], [data-hover-media], [data-preview-media]'
    );
    if (mediaTriggers.length > 0) {
        const mediaUrls = [];
        let triggerSelector = '[data-video="to-play"]';
        let sampleRect = { top: 0, left: 0, width: 0, height: 0 };
        mediaTriggers.forEach((el, idx) => {
            if (idx === 0) {
                const tag = el.tagName.toLowerCase();
                const cls = typeof el.className === 'string' ? el.className.split(/\\s+/)[0] : '';
                triggerSelector = cls ? `${tag}.${cls}[data-video]` : `${tag}[data-video]`;
                const r = el.getBoundingClientRect();
                sampleRect = {
                    top: Math.round(r.top + window.scrollY),
                    left: Math.round(r.left),
                    width: Math.round(r.width),
                    height: Math.round(r.height)
                };
            }
            const vid = el.getAttribute('data-thumb-video') || el.getAttribute('data-video-url') || el.getAttribute('data-video');
            if (vid && mediaUrls.length < 5) {
                mediaUrls.push(vid);
            }
        });

        results.hover_media_switchers.push({
            trigger_selector: triggerSelector,
            target_selector: '.hero-visual, .media-preview-container',
            media_samples: mediaUrls,
            count: mediaTriggers.length,
            dimensions: sampleRect,
            description: `Dynamic media switcher where hovering ${mediaTriggers.length} list items dynamically switches active preview media.`
        });
    }

    // 11. Scroll Parallax Tracks ([data-move], [data-parallax])
    const parallaxEls = document.querySelectorAll('[data-move], [data-move-sc], [data-parallax], [data-scroll-speed]');
    if (parallaxEls.length > 0) {
        const modes = new Set();
        let sampleSelector = '[data-move]';
        let sampleRect = { top: 0, left: 0, width: 0, height: 0 };
        parallaxEls.forEach((el, idx) => {
            const m = el.getAttribute('data-move') || el.getAttribute('data-parallax') || 'vertical';
            modes.add(m);
            if (idx === 0) {
                const tag = el.tagName.toLowerCase();
                const cls = typeof el.className === 'string' ? el.className.split(/\\s+/)[0] : '';
                sampleSelector = cls ? `${tag}.${cls}[data-move]` : `${tag}[data-move]`;
                const r = el.getBoundingClientRect();
                sampleRect = {
                    top: Math.round(r.top + window.scrollY),
                    left: Math.round(r.left),
                    width: Math.round(r.width),
                    height: Math.round(r.height)
                };
            }
        });

        results.scroll_parallaxes.push({
            selector: sampleSelector,
            move_mode: Array.from(modes).join(', '),
            count: parallaxEls.length,
            dimensions: sampleRect,
            description: `Scroll-driven parallax motion on ${parallaxEls.length} elements (modes: ${Array.from(modes).join(', ')}).`
        });
    }

    return results;
}
"""


def detect_and_explore_features(
    page: Page,
    explore: bool = True,
) -> SophisticatedFeaturesSummary:
    """Probes the live page for sophisticated animation, sticky scenes, drawers, and interactive systems."""
    try:
        raw_probe: dict[str, Any] = page.evaluate(PROBE_SOPHISTICATED_FEATURES_JS)
    except Exception as e:
        logger.warning("Feature detection probe failed: %s", e)
        return SophisticatedFeaturesSummary(
            has_sophisticated_features=False,
            detected_engines=[],
            features=[],
            recommended_explorations=[],
        )

    features: list[SophisticatedFeature] = []
    recommended: list[str] = []

    detected_engines = raw_probe.get("detected_engines", [])
    if detected_engines:
        features.append(
            SophisticatedFeature(
                category="motion_physics_engine",
                name="Motion & Physics Runtime",
                selector="window",
                description=f"Active animation frameworks: {', '.join(detected_engines)}",
                dimensions={},
                requires_independent_exploration=False,
                exploration_status="explored",
                suggested_implementation="Integrate Framer Motion (`useScroll`, `useTransform`) or GSAP ScrollTrigger for fidelity.",
                details={"engines": detected_engines},
            )
        )
        recommended.append(
            f"Page uses {', '.join(detected_engines)}. Model transitions using declarative motion primitives (e.g. Framer Motion layout animations)."
        )

    # Sticky tracks
    sticky_tracks = raw_probe.get("sticky_tracks", [])
    for track in sticky_tracks:
        features.append(
            SophisticatedFeature(
                category="sticky_scroll_track",
                name=track["name"],
                selector=track["selector"],
                description=track["description"],
                dimensions=track["dimensions"],
                requires_independent_exploration=True,
                exploration_status="explored" if explore else "detected",
                suggested_implementation="Replicate with CSS `position: sticky; top: 0` container inside a relative scroll track, or Framer Motion `useScroll()` with sequence steps.",
                details={
                    "stickyHeight": track.get("stickyHeight"),
                    "childItemsCount": track.get("childItemsCount"),
                },
            )
        )
        recommended.append(
            f"Explore {track['selector']}: Pinned scene spanning {track['dimensions'].get('height', 0)}px scroll track. Do not flatten into static cards; preserve sticky phase-switching."
        )

    # Offscreen drawers & modals
    drawers = raw_probe.get("offscreen_drawers", [])
    for drawer in drawers:
        features.append(
            SophisticatedFeature(
                category="offscreen_drawer",
                name=drawer["name"],
                selector=drawer["selector"],
                description=drawer["description"],
                dimensions=drawer["dimensions"],
                requires_independent_exploration=True,
                exploration_status="explored" if explore else "detected",
                suggested_implementation="Implement as an isolated Dialog/Drawer component (e.g. Radix UI Dialog or Vaul drawer) triggered by card click.",
                details={
                    "headings": drawer.get("headings", []),
                    "textPreview": drawer.get("textPreview", ""),
                },
            )
        )
        recommended.append(
            f"Explore {drawer['selector']}: Deep off-screen drawer container ({drawer['dimensions'].get('height', 0)}px tall) holding dynamic case study or form details."
        )

    # Interactive disclosures
    disclosures = raw_probe.get("disclosures", [])
    for disc in disclosures:
        features.append(
            SophisticatedFeature(
                category="interactive_disclosure",
                name=disc["name"],
                selector=disc["selector"],
                description=disc["description"],
                dimensions=disc["dimensions"],
                requires_independent_exploration=False,
                exploration_status="explored",
                suggested_implementation="Implement using `@radix-ui/react-accordion` or HTML5 `<details>` with Framer Motion `AnimatePresence` height animation.",
                details={"title": disc.get("title")},
            )
        )

    # Marquees
    marquees = raw_probe.get("marquees", [])
    for mq in marquees:
        features.append(
            SophisticatedFeature(
                category="marquee_stream",
                name=mq["name"],
                selector=mq["selector"],
                description=mq["description"],
                dimensions=mq["dimensions"],
                requires_independent_exploration=False,
                exploration_status="explored",
                suggested_implementation="Replicate with CSS infinite marquee animation (`@keyframes marquee { 0% { transform: translateX(0); } 100% { transform: translateX(-50%); } }`) with duplicated items.",
                details={},
            )
        )

    # Canvases
    canvases = raw_probe.get("canvases", [])
    for c in canvases:
        features.append(
            SophisticatedFeature(
                category="webgl_canvas",
                name=c["name"],
                selector=c["selector"],
                description=c["description"],
                dimensions=c["dimensions"],
                requires_independent_exploration=True,
                exploration_status="explored",
                suggested_implementation="Wrap with `@react-three/fiber` / `@react-three/drei` Canvas for WebGL or HTML5 Canvas with requestAnimationFrame loop.",
                details={"isWebGL": c.get("isWebGL")},
            )
        )

    # Kinetic Magnetic Elements
    magnetic_elements = raw_probe.get("magnetic_elements", [])
    for mag in magnetic_elements:
        features.append(
            SophisticatedFeature(
                category="magnetic_physics",
                name=f"Magnetic Cursor Pull Physics ({mag['selector']})",
                selector=mag["selector"],
                description=mag["description"],
                dimensions=mag.get("dimensions", {}),
                requires_independent_exploration=False,
                exploration_status="explored",
                suggested_implementation=(
                    "Implement with GSAP quickTo or Framer Motion spring physics. On mousemove over target, "
                    "translate target towards pointer ((clientX - center) / width * strength); on mouseleave, "
                    "spring back with elastic.out(1, 0.3)."
                ),
                details={
                    "strength": mag.get("strength"),
                    "sample_labels": mag.get("sample_labels", []),
                    "count": mag.get("count", 1),
                },
            )
        )
        recommended.append(
            f"Add magnetic physics to buttons ({mag['selector']}): Wire elastic spring translation towards pointer on hover."
        )

    # Custom Interactive Cursors
    custom_cursors = raw_probe.get("custom_cursors", [])
    for cur in custom_cursors:
        features.append(
            SophisticatedFeature(
                category="custom_cursor",
                name=f"Custom Interactive Cursor & Floating Preview ({cur['selector']})",
                selector=cur["selector"],
                description=cur["description"],
                dimensions=cur.get("dimensions", {}),
                requires_independent_exploration=False,
                exploration_status="explored",
                suggested_implementation=(
                    "Render fixed pointer-events-none cursor follower with lerp coordinates. "
                    "Expand circle on button hover ([data-cursor='btn']) and display floating thumbnail card "
                    "when hovering links with [data-cursor-img]."
                ),
                details={
                    "states": cur.get("states", []),
                    "has_image_preview": cur.get("has_image_preview", False),
                    "preview_count": cur.get("preview_count", 0),
                    "sample_preview_images": cur.get("sample_preview_images", []),
                },
            )
        )
        if cur.get("has_image_preview"):
            recommended.append(
                f"Implement floating cursor card preview for project rows ({cur['selector']}): Render thumbnail when hovering [data-cursor-img] targets."
            )

    # Typographic SplitText Reveals
    typographic_reveals = raw_probe.get("typographic_reveals", [])
    for tr in typographic_reveals:
        features.append(
            SophisticatedFeature(
                category="typographic_reveal",
                name=f"SplitText Typographic Reveal ({tr['selector']})",
                selector=tr["selector"],
                description=tr["description"],
                dimensions=tr.get("dimensions", {}),
                requires_independent_exploration=False,
                exploration_status="explored",
                suggested_implementation=(
                    "Split heading text into word/character spans inside overflow-hidden wrappers. "
                    "Animate yPercent: 60 -> 0, opacity: 0 -> 1 with stagger: 0.02 upon viewport entry."
                ),
                details={
                    "split_type": tr.get("split_type"),
                    "sample_text": tr.get("text_preview"),
                },
            )
        )
        recommended.append(
            f"Animate typographic entrance for {tr['selector']}: Stagger characters or words upward using overflow masks."
        )

    # Dynamic Hover Media Switchers
    hover_media_switchers = raw_probe.get("hover_media_switchers", [])
    for hms in hover_media_switchers:
        features.append(
            SophisticatedFeature(
                category="hover_media_switcher",
                name=f"Dynamic Hover Media Switcher ({hms['trigger_selector']})",
                selector=hms["trigger_selector"],
                description=hms["description"],
                dimensions=hms.get("dimensions", {}),
                requires_independent_exploration=True,
                exploration_status="explored" if explore else "detected",
                suggested_implementation=(
                    "Listen to mouseenter on client/project list items to dynamically switch and play "
                    "the associated video loop or image preview in the central media viewer container."
                ),
                details={
                    "target_selector": hms.get("target_selector"),
                    "media_samples": hms.get("media_samples", []),
                    "count": hms.get("count", 1),
                },
            )
        )
        recommended.append(
            f"Wire dynamic media switcher on {hms['trigger_selector']}: Hovering items swaps and plays background video."
        )

    # Scroll Parallax Tracks
    scroll_parallaxes = raw_probe.get("scroll_parallaxes", [])
    for sp in scroll_parallaxes:
        features.append(
            SophisticatedFeature(
                category="scroll_parallax",
                name=f"Scroll Parallax Track ({sp['selector']})",
                selector=sp["selector"],
                description=sp["description"],
                dimensions=sp.get("dimensions", {}),
                requires_independent_exploration=False,
                exploration_status="explored",
                suggested_implementation=(
                    "Apply continuous scroll translation (transform: translateY) at customized scrub speeds "
                    "using Lenis scroll event or GSAP ScrollTrigger."
                ),
                details={
                    "move_mode": sp.get("move_mode"),
                    "count": sp.get("count", 1),
                },
            )
        )

    has_features = len(features) > 0

    return SophisticatedFeaturesSummary(
        has_sophisticated_features=has_features,
        detected_engines=detected_engines,
        features=features,
        recommended_explorations=recommended,
    )
