"""Detection and isolated exploration of sophisticated UI features and motion engines."""

from __future__ import annotations

import logging
import math
from typing import Any

from playwright.sync_api import Page

from ui_sleuth.asset_inspector import CANVAS_CONTEXT_HELPER_JS
from ui_sleuth.models import SophisticatedFeature, SophisticatedFeaturesSummary

logger = logging.getLogger(__name__)

# Per-category cap on reported features; the remainder is summarised in one "+N more" entry.
MAX_FEATURES_PER_CATEGORY = 5

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

    const SAFE_CLASS = /^[A-Za-z_][\\w-]*$/;

    // Selector built from the attribute that actually matched (falling back to a matching class
    // token), so the reported selector always matches the element it was derived from.
    function matchedSelector(el, attrs, classHint) {
        const tag = el.tagName.toLowerCase();
        for (const a of attrs) {
            if (el.hasAttribute(a)) return `${tag}[${a}]`;
        }
        if (typeof el.className === 'string') {
            const hit = el.className.split(/\\s+/).find(c => SAFE_CLASS.test(c) && c.includes(classHint));
            if (hit) return `${tag}.${hit}`;
        }
        return makeElementSelector(el);
    }

    // Group elements by selector: first element supplies dimensions, `count` is the group size.
    function groupBySelector(items) {
        const groups = new Map();
        for (const item of items) {
            const existing = groups.get(item.selector);
            if (existing) {
                existing.count += 1;
            } else {
                groups.set(item.selector, Object.assign({ count: 1 }, item));
            }
        }
        return Array.from(groups.values());
    }

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
        // data-framer-* attributes identify pages published by the Framer site builder; they say
        // nothing about whether the Framer Motion library is used.
        results.detected_engines.push('Framer (site builder)');
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
    const drawerEls = new Set();
    const drawerItems = [];
    for (const el of allEls) {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        const transform = style.transform || '';
        // Document coordinates: an element merely above the current viewport is not "offscreen"
        const docTop = rect.top + window.scrollY;
        const docBottom = rect.bottom + window.scrollY;
        const docHeight = Math.max(
            document.documentElement.scrollHeight,
            document.body ? document.body.scrollHeight : 0,
            viewportHeight
        );
        const isOffscreenTransform = transform.includes('matrix') && (
            // translated outside the document bounds
            docTop > docHeight + viewportHeight || rect.left > viewportWidth || rect.right < 0 || docBottom < -100
        );
        const clsStr = (el.className && typeof el.className === 'string') ? el.className : '';
        // Whole class tokens, so `newsletter` or `renewsletter` do not count as `news`.
        const clsTokens = clsStr.split(/[-_\\s]+/).filter(Boolean);
        const hasOverlayClass = (
            clsStr.includes('modal') ||
            clsStr.includes('drawer') ||
            clsStr.includes('popup') ||
            clsStr.includes('pop-') ||
            clsTokens.includes('news') ||
            /\\b(nav|menu-overlay)\\b/.test(clsStr)
        );
        const isHiddenMenuOverlay = (
            (style.position === 'fixed' || style.position === 'absolute') &&
            (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity || '1') === 0) &&
            hasOverlayClass &&
            el.querySelectorAll('a, button, [class*="menu"], [class*="item"]').length >= 4
        );
        const isLargeModal = ((rect.height > 500 || el.scrollHeight > 500) && (
            style.position === 'fixed' || style.position === 'absolute'
        ) && (
            style.display === 'none' ||
            style.visibility === 'hidden' ||
            parseFloat(style.opacity || '1') === 0 ||
            isOffscreenTransform ||
            hasOverlayClass
        )) || isHiddenMenuOverlay;

        if (isLargeModal) {
            const selector = makeElementSelector(el);
            const dedupeKey = el.id ? selector : `${selector}@${Math.round(rect.top + window.scrollY)}`;

            if (!seenDrawers.has(dedupeKey) && selector !== 'body' && selector !== 'html') {
                seenDrawers.add(dedupeKey);
                drawerEls.add(el);
                const headings = Array.from(el.querySelectorAll('h1, h2, h3, [class*="title"], [class*="menu-link-item"]'))
                    .map(h => (h.innerText || h.textContent || '').trim().replace(/\\s+/g, ' '))
                    .filter(t => t.length > 0)
                    .slice(0, 8);
                const textPreview = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 150);

                drawerItems.push({
                    el: el,
                    name: `Modal/Drawer (${selector})`,
                    selector: selector,
                    dimensions: {
                        top: Math.round(rect.top + window.scrollY),
                        left: Math.round(rect.left),
                        width: Math.round(rect.width || el.scrollWidth || viewportWidth),
                        height: Math.round(rect.height || el.scrollHeight || viewportHeight)
                    },
                    headings: headings,
                    textPreview: textPreview,
                    description: `Off-screen or dynamic drawer/modal containing ${headings.length} headings/items and substantial structured content.`
                });
            }
        }
    }

    // Keep only the outermost drawer (nested drawers are parts of the same component).
    const outerDrawers = drawerItems.filter(item => {
        for (let p = item.el.parentElement; p; p = p.parentElement) {
            if (drawerEls.has(p)) return false;
        }
        return true;
    });
    for (const g of groupBySelector(outerDrawers)) {
        delete g.el;
        results.offscreen_drawers.push(g);
    }

    // 4. Interactive Disclosures & Accordions
    const ACCORDION_SEL = 'details, [aria-expanded], [class*="accordion"], [class*="dropdown"], [data-accordion]';
    const accordionTriggers = document.querySelectorAll(ACCORDION_SEL);
    const disclosureItems = [];
    for (const el of accordionTriggers) {
        if (el.parentElement && el.parentElement.closest(ACCORDION_SEL)) continue;
        const selector = makeElementSelector(el);
        const r = el.getBoundingClientRect();
        {
            const titleEl = el.querySelector('summary, [class*="title"], [class*="head"], h1, h2, h3, h4');
            const title = titleEl ? (titleEl.innerText || '').trim() : '';
            disclosureItems.push({
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
    results.disclosures.push(...groupBySelector(disclosureItems));

    // 5. Marquees & Continuous Tickers
    const MARQUEE_SEL = '[class*="marquee"], [class*="ticker"], [class*="client-list"]';
    const marquees = document.querySelectorAll(MARQUEE_SEL);
    const marqueeItems = [];
    for (const el of marquees) {
        // Outermost only: the track and every item inside a marquee match the same selector.
        if (el.parentElement && el.parentElement.closest(MARQUEE_SEL)) continue;
        const selector = makeElementSelector(el);
        const r = el.getBoundingClientRect();
        marqueeItems.push({
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
    results.marquees.push(...groupBySelector(marqueeItems));

    // 6. Dynamic Canvases
    const canvases = document.querySelectorAll('canvas');
    for (const c of canvases) {
        const gl = sleuthCanvasContext(c).is_webgl;
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
                sampleSelector = matchedSelector(el, ['data-magnetic', 'data-magnetic-strength'], 'magnetic');
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

        // Only report a cursor element selector when one was actually found.
        let cursorSelector = cursorInteractiveEls.length > 0 ? '[data-cursor]' : 'body';
        let cursorRect = { top: 0, left: 0, width: 24, height: 24 };
        if (cursorEls.length > 0) {
            const first = cursorEls[0];
            cursorSelector = makeElementSelector(first);
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
            element_detected: cursorEls.length > 0,
            dimensions: cursorRect,
            states: Array.from(states),
            has_image_preview: previewCount > 0,
            preview_count: previewCount,
            sample_preview_images: previewImages,
            description: `Interactive cursor follower supporting ${states.size} dynamic state(s) (${Array.from(states).join(', ') || 'default'}) and ${previewCount} floating hover image card(s).`
        });
    }

    // 9. Typographic SplitText Reveals
    // Explicit split-text markers, plus generic headings that already contain split word/char/line spans.
    const SPLIT_MARKERS = '.split-text, [data-split], .words, .chars, .lines';
    const SPLIT_CHILDREN = '[class*="char"], [class*="word"], [class*="split"], [data-word], [data-char]';
    const splitCandidates = Array.from(document.querySelectorAll(SPLIT_MARKERS + ', h1, h2, h3'))
        .filter(el => el.matches(SPLIT_MARKERS) || el.querySelector(SPLIT_CHILDREN));
    const seenSplit = new Map();
    splitCandidates.forEach(el => {
        const tag = el.tagName.toLowerCase();
        const cls = typeof el.className === 'string' ? el.className.split(/\\s+/).find(c => SAFE_CLASS.test(c)) : '';
        const selector = cls ? `${tag}.${cls}` : tag;
        if (seenSplit.has(selector)) {
            seenSplit.get(selector).count += 1;
        } else if (seenSplit.size < 6) {
            const r = el.getBoundingClientRect();
            const txt = (el.innerText || '').trim().slice(0, 80).replace(/[\\r\\n]+/g, ' ');
            const hasNestedChars = el.querySelectorAll('.char, .chars, [class*="char"]').length > 0;
            const hasNestedWords = el.querySelectorAll('.word, .words, [class*="word"]').length > 0;
            const splitType = hasNestedChars ? 'characters' : (hasNestedWords ? 'words' : 'lines/words');

            const entry = {
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
            };
            seenSplit.set(selector, entry);
            results.typographic_reveals.push(entry);
        }
    });

    // 10. Hover Dynamic Media Switchers
    const mediaTriggers = document.querySelectorAll(
        '[data-video="to-play"], [data-thumb-video], [data-video-target], [data-hover-media], [data-preview-media]'
    );
    if (mediaTriggers.length > 0) {
        const mediaUrls = [];
        const MEDIA_ATTRS = ['data-video', 'data-thumb-video', 'data-video-target', 'data-hover-media', 'data-preview-media'];
        let triggerSelector = '[data-video]';
        let targetSelector = null;
        let sampleRect = { top: 0, left: 0, width: 0, height: 0 };
        mediaTriggers.forEach((el, idx) => {
            if (idx === 0) {
                triggerSelector = matchedSelector(el, MEDIA_ATTRS, 'video');
                // The preview target is only reported when the trigger declares it and it resolves.
                const declared = el.getAttribute('data-video-target');
                if (declared) {
                    try {
                        if (document.querySelector(declared)) targetSelector = declared;
                    } catch (e) { /* not a valid selector */ }
                }
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
            target_selector: targetSelector,
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
                sampleSelector = matchedSelector(el, ['data-move', 'data-move-sc', 'data-parallax', 'data-scroll-speed'], 'parallax');
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
""".replace("() => {", "() => {" + CANVAS_CONTEXT_HELPER_JS, 1)


def _coerce_int(value: Any) -> int | None:
    """Coerce a probe number to int; None for bool, NaN, +/-Infinity or non-numerics."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return int(value)
    return None


def _clean_dims(value: Any) -> dict[str, int]:
    """Return only finite numeric dimension entries as ints (drops malformed ones)."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, int] = {}
    for key, raw in value.items():
        num = _coerce_int(raw)
        if isinstance(key, str) and num is not None:
            out[key] = num
    return out


def _entries(
    raw_probe: dict[str, Any], key: str, required: str = "selector"
) -> list[dict[str, Any]]:
    """Return well-formed (dict with a string `required` key) entries; skip the rest."""
    raw = raw_probe.get(key)
    if not isinstance(raw, list):
        return []
    good: list[dict[str, Any]] = []
    for entry in raw:
        if isinstance(entry, dict) and isinstance(entry.get(required), str):
            good.append(entry)
        else:
            logger.debug("Skipping malformed %s probe entry: %.80r", key, entry)
    return good


def _text(entry: dict[str, Any], key: str, default: str = "") -> str:
    value = entry.get(key)
    return value if isinstance(value, str) else default


def _group_and_cap(
    entries: list[dict[str, Any]], key_fields: tuple[str, ...] = ("selector",)
) -> tuple[list[dict[str, Any]], int, list[str]]:
    """Merge entries with the same key (summing `count`), then keep the first N groups.

    Returns (kept entries with a merged `count`, omitted group count, omitted selectors).
    """
    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    for entry in entries:
        key = tuple(_text(entry, f) for f in key_fields)
        count = _coerce_int(entry.get("count"))
        count = count if count is not None and count > 0 else 1
        if key in groups:
            groups[key]["count"] += count
        else:
            groups[key] = {**entry, "count": count}
    merged = list(groups.values())
    kept = merged[:MAX_FEATURES_PER_CATEGORY]
    rest = merged[MAX_FEATURES_PER_CATEGORY:]
    omitted_total = sum(int(e["count"]) for e in rest)
    return kept, omitted_total, [str(e.get("selector", "")) for e in rest[:5]]


def _overflow_feature(
    category: Any, omitted: int, selectors: list[str]
) -> SophisticatedFeature | None:
    """Single summary entry standing in for features dropped by the per-category cap."""
    if omitted <= 0:
        return None
    label = str(category).replace("_", " ")
    return SophisticatedFeature(
        category=category,
        name=f"+{omitted} more {label}",
        selector=f"(+{omitted} more)",
        description=f"{omitted} additional {label} element(s) omitted (cap of {MAX_FEATURES_PER_CATEGORY} per category).",
        dimensions={},
        requires_independent_exploration=False,
        exploration_status="detected",
        suggested_implementation="",
        details={"omitted": omitted, "omitted_selectors": selectors},
    )


def detect_and_explore_features(
    page: Page,
    explore: bool = True,
) -> SophisticatedFeaturesSummary:
    """Probe the live page for sophisticated animation, sticky scenes, drawers and interactions.

    Detection only: nothing is clicked, hovered or expanded, so every feature reports
    ``detected``. `explore` is accepted for forward compatibility and currently has no effect.
    """
    del explore  # reserved; no exploration routine exists yet
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

    if not isinstance(raw_probe, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
        raw_probe = {}
    engines_raw = raw_probe.get("detected_engines")
    detected_engines = (
        [e for e in engines_raw if isinstance(e, str)] if isinstance(engines_raw, list) else []
    )
    if detected_engines:
        features.append(
            SophisticatedFeature(
                category="motion_physics_engine",
                name="Motion & Physics Runtime",
                selector="window",
                description=f"Active animation frameworks: {', '.join(detected_engines)}",
                dimensions={},
                requires_independent_exploration=False,
                exploration_status="detected",
                suggested_implementation="Integrate Framer Motion (`useScroll`, `useTransform`) or GSAP ScrollTrigger for fidelity.",
                details={"engines": detected_engines},
            )
        )
        recommended.append(
            f"Page uses {', '.join(detected_engines)}. Model transitions using declarative motion primitives (e.g. Framer Motion layout animations)."
        )

    # Sticky tracks
    sticky_tracks, sticky_omitted, sticky_rest = _group_and_cap(
        _entries(raw_probe, "sticky_tracks")
    )
    for track in sticky_tracks:
        features.append(
            SophisticatedFeature(
                category="sticky_scroll_track",
                name=_text(track, "name", track.get("selector", "")),
                selector=track["selector"],
                description=_text(track, "description"),
                dimensions=_clean_dims(track.get("dimensions")),
                requires_independent_exploration=True,
                exploration_status="detected",
                suggested_implementation="Replicate with CSS `position: sticky; top: 0` container inside a relative scroll track, or Framer Motion `useScroll()` with sequence steps.",
                details={
                    "stickyHeight": _coerce_int(track.get("stickyHeight")),
                    "childItemsCount": _coerce_int(track.get("childItemsCount")),
                    "count": track["count"],
                },
            )
        )
        recommended.append(
            f"Explore {track['selector']}: Pinned scene spanning {_clean_dims(track.get('dimensions')).get('height', 0)}px scroll track. Do not flatten into static cards; preserve sticky phase-switching."
        )

    if overflow := _overflow_feature("sticky_scroll_track", sticky_omitted, sticky_rest):
        features.append(overflow)

    # Offscreen drawers & modals
    drawers, drawer_omitted, drawer_rest = _group_and_cap(_entries(raw_probe, "offscreen_drawers"))
    for drawer in drawers:
        features.append(
            SophisticatedFeature(
                category="offscreen_drawer",
                name=_text(drawer, "name", drawer.get("selector", "")),
                selector=drawer["selector"],
                description=_text(drawer, "description"),
                dimensions=_clean_dims(drawer.get("dimensions")),
                requires_independent_exploration=True,
                exploration_status="detected",
                suggested_implementation="Implement as an isolated Dialog/Drawer component (e.g. Radix UI Dialog or Vaul drawer) triggered by card click.",
                details={
                    "headings": drawer.get("headings", []),
                    "textPreview": drawer.get("textPreview", ""),
                    "count": drawer["count"],
                },
            )
        )
        recommended.append(
            f"Explore {drawer['selector']}: Deep off-screen drawer container ({_clean_dims(drawer.get('dimensions')).get('height', 0)}px tall) holding dynamic case study or form details."
        )

    if overflow := _overflow_feature("offscreen_drawer", drawer_omitted, drawer_rest):
        features.append(overflow)

    # Interactive disclosures
    disclosures, disc_omitted, disc_rest = _group_and_cap(_entries(raw_probe, "disclosures"))
    for disc in disclosures:
        features.append(
            SophisticatedFeature(
                category="interactive_disclosure",
                name=_text(disc, "name", disc.get("selector", "")),
                selector=disc["selector"],
                description=_text(disc, "description"),
                dimensions=_clean_dims(disc.get("dimensions")),
                requires_independent_exploration=False,
                exploration_status="detected",
                suggested_implementation="Implement using `@radix-ui/react-accordion` or HTML5 `<details>` with Framer Motion `AnimatePresence` height animation.",
                details={"title": disc.get("title"), "count": disc["count"]},
            )
        )
    if overflow := _overflow_feature("interactive_disclosure", disc_omitted, disc_rest):
        features.append(overflow)

    # Marquees
    marquees, mq_omitted, mq_rest = _group_and_cap(_entries(raw_probe, "marquees"))
    for mq in marquees:
        features.append(
            SophisticatedFeature(
                category="marquee_stream",
                name=_text(mq, "name", mq.get("selector", "")),
                selector=mq["selector"],
                description=_text(mq, "description"),
                dimensions=_clean_dims(mq.get("dimensions")),
                requires_independent_exploration=False,
                exploration_status="detected",
                suggested_implementation="Replicate with CSS infinite marquee animation (`@keyframes marquee { 0% { transform: translateX(0); } 100% { transform: translateX(-50%); } }`) with duplicated items.",
                details={"count": mq["count"]},
            )
        )
    if overflow := _overflow_feature("marquee_stream", mq_omitted, mq_rest):
        features.append(overflow)

    # Canvases
    canvases, canvas_omitted, canvas_rest = _group_and_cap(
        _entries(raw_probe, "canvases"), ("selector", "name")
    )
    for c in canvases:
        features.append(
            SophisticatedFeature(
                category="webgl_canvas",
                name=_text(c, "name", c.get("selector", "")),
                selector=c["selector"],
                description=_text(c, "description"),
                dimensions=_clean_dims(c.get("dimensions")),
                requires_independent_exploration=True,
                exploration_status="detected",
                suggested_implementation="Wrap with `@react-three/fiber` / `@react-three/drei` Canvas for WebGL or HTML5 Canvas with requestAnimationFrame loop.",
                details={"isWebGL": c.get("isWebGL"), "count": c["count"]},
            )
        )
    if overflow := _overflow_feature("webgl_canvas", canvas_omitted, canvas_rest):
        features.append(overflow)

    # Kinetic Magnetic Elements
    magnetic_elements = _entries(raw_probe, "magnetic_elements")
    for mag in magnetic_elements:
        features.append(
            SophisticatedFeature(
                category="magnetic_physics",
                name=f"Magnetic Cursor Pull Physics ({mag['selector']})",
                selector=mag["selector"],
                description=_text(mag, "description"),
                dimensions=_clean_dims(mag.get("dimensions")),
                requires_independent_exploration=False,
                exploration_status="detected",
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
    custom_cursors = _entries(raw_probe, "custom_cursors")
    for cur in custom_cursors:
        features.append(
            SophisticatedFeature(
                category="custom_cursor",
                name=f"Custom Interactive Cursor & Floating Preview ({cur['selector']})",
                selector=cur["selector"],
                description=_text(cur, "description"),
                dimensions=_clean_dims(cur.get("dimensions")),
                requires_independent_exploration=False,
                exploration_status="detected",
                suggested_implementation=(
                    "Render fixed pointer-events-none cursor follower with lerp coordinates. "
                    "Expand circle on button hover ([data-cursor='btn']) and display floating thumbnail card "
                    "when hovering links with [data-cursor-img]."
                ),
                details={
                    "element_detected": cur.get("element_detected") is True,
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
    typographic_reveals, tr_omitted, tr_rest = _group_and_cap(
        _entries(raw_probe, "typographic_reveals")
    )
    for tr in typographic_reveals:
        features.append(
            SophisticatedFeature(
                category="typographic_reveal",
                name=f"SplitText Typographic Reveal ({tr['selector']})",
                selector=tr["selector"],
                description=_text(tr, "description"),
                dimensions=_clean_dims(tr.get("dimensions")),
                requires_independent_exploration=False,
                exploration_status="detected",
                suggested_implementation=(
                    "Split heading text into word/character spans inside overflow-hidden wrappers. "
                    "Animate yPercent: 60 -> 0, opacity: 0 -> 1 with stagger: 0.02 upon viewport entry."
                ),
                details={
                    "split_type": tr.get("split_type"),
                    "sample_text": tr.get("text_preview"),
                    "count": tr["count"],
                },
            )
        )
        recommended.append(
            f"Animate typographic entrance for {tr['selector']}: Stagger characters or words upward using overflow masks."
        )

    if overflow := _overflow_feature("typographic_reveal", tr_omitted, tr_rest):
        features.append(overflow)

    # Dynamic Hover Media Switchers
    hover_media_switchers = _entries(raw_probe, "hover_media_switchers", "trigger_selector")
    for hms in hover_media_switchers:
        features.append(
            SophisticatedFeature(
                category="hover_media_switcher",
                name=f"Dynamic Hover Media Switcher ({hms['trigger_selector']})",
                selector=hms["trigger_selector"],
                description=_text(hms, "description"),
                dimensions=_clean_dims(hms.get("dimensions")),
                requires_independent_exploration=True,
                exploration_status="detected",
                suggested_implementation=(
                    "Listen to mouseenter on client/project list items to dynamically switch and play "
                    "the associated video loop or image preview in the central media viewer container."
                ),
                details={
                    # Only present when the page actually declared a resolvable target.
                    **(
                        {"target_selector": hms["target_selector"]}
                        if isinstance(hms.get("target_selector"), str) and hms["target_selector"]
                        else {}
                    ),
                    "media_samples": hms.get("media_samples", []),
                    "count": hms.get("count", 1),
                },
            )
        )
        recommended.append(
            f"Wire dynamic media switcher on {hms['trigger_selector']}: Hovering items swaps and plays background video."
        )

    # Scroll Parallax Tracks
    scroll_parallaxes = _entries(raw_probe, "scroll_parallaxes")
    for sp in scroll_parallaxes:
        features.append(
            SophisticatedFeature(
                category="scroll_parallax",
                name=f"Scroll Parallax Track ({sp['selector']})",
                selector=sp["selector"],
                description=_text(sp, "description"),
                dimensions=_clean_dims(sp.get("dimensions")),
                requires_independent_exploration=False,
                exploration_status="detected",
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
