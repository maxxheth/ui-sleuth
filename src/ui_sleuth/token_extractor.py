"""Design token harvesting module using client-side JavaScript evaluation."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from ui_sleuth.models import (
    ColorPalette,
    ColorToken,
    DesignTokens,
    HeaderScale,
    SpacingElevationTokens,
    TypographyTokens,
)

if TYPE_CHECKING:
    from playwright.sync_api import Page

logger = logging.getLogger(__name__)

# Complete client-side JS evaluation script
HARVEST_TOKENS_JS = r"""
() => {
    const colorCanvas = document.createElement('canvas');
    colorCanvas.width = 1;
    colorCanvas.height = 1;
    let colorCtx = null;
    try { colorCtx = colorCanvas.getContext('2d', { willReadFrequently: true }); } catch (e) {}

    // Two distinct sentinels: an unparseable value leaves the canvas on the sentinel we set, so
    // assigning the same value after each sentinel yields different results only when it was
    // rejected. A legit black/white (e.g. oklch(0 0 0)) yields the same result both times.
    const SENTINEL_A = '#000000';
    const SENTINEL_B = '#ffffff';

    // Resolve any CSS color string to sRGB {r, g, b, a} by painting it on the 1x1 canvas and
    // reading the pixel back. Modern Chromium keeps oklch()/color() unconverted in
    // ctx.fillStyle, so the pixel readback is what actually yields sRGB bytes.
    function canvasResolve(str) {
        if (!colorCtx) return null;
        try {
            colorCtx.fillStyle = SENTINEL_A;
            colorCtx.fillStyle = str;
            const viaA = colorCtx.fillStyle;
            colorCtx.fillStyle = SENTINEL_B;
            colorCtx.fillStyle = str;
            const viaB = colorCtx.fillStyle;
            if (viaA !== viaB) return null;  // rejected: canvas kept whichever sentinel we set
            colorCtx.clearRect(0, 0, 1, 1);
            colorCtx.fillRect(0, 0, 1, 1);
            const d = colorCtx.getImageData(0, 0, 1, 1).data;
            return { r: d[0], g: d[1], b: d[2], a: d[3] / 255 };
        } catch (e) {
            return null;
        }
    }

    const HEX_RE = /^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/;
    const RGB_RE = /^rgba?\(\s*([\d.]+)(%?)[,\s]+([\d.]+)(%?)[,\s]+([\d.]+)(%?)\s*(?:[,\/]\s*([\d.]+)(%?))?\s*\)$/;

    // Returns {r, g, b, a} (0-255, alpha 0-1) or null if the string is not a usable color.
    function parseSimpleColor(str) {
        const hm = str.match(HEX_RE);
        if (hm) {
            let h = hm[1];
            if (h.length <= 4) h = h.split('').map(c => c + c).join('');
            return {
                r: parseInt(h.slice(0, 2), 16),
                g: parseInt(h.slice(2, 4), 16),
                b: parseInt(h.slice(4, 6), 16),
                a: h.length === 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1
            };
        }
        const m = str.match(RGB_RE);
        if (m) {
            const ch = (v, pct) => Math.max(0, Math.min(255, Math.round(pct ? parseFloat(v) * 2.55 : parseFloat(v))));
            let a = 1;
            if (m[7] !== undefined) a = m[8] ? parseFloat(m[7]) / 100 : parseFloat(m[7]);
            return { r: ch(m[1], m[2]), g: ch(m[3], m[4]), b: ch(m[5], m[6]), a: Math.max(0, Math.min(1, a)) };
        }
        return null;
    }

    function rgbToHex(rgbStr) {
        if (!rgbStr) return null;
        const str = String(rgbStr).trim().toLowerCase();
        if (!str || str === 'transparent') return null;

        let c = parseSimpleColor(str);
        if (!c) {
            // Modern CSS color functions (oklch, lab, color(), hsl, named): normalize via canvas
            c = canvasResolve(str);
        }
        if (!c) return null;
        if (c.a < 0.1) return null;  // effectively invisible

        const h2 = n => n.toString(16).padStart(2, '0');
        let hex = '#' + h2(c.r) + h2(c.g) + h2(c.b);
        if (c.a < 1) hex += h2(Math.round(c.a * 255));  // keep alpha as #rrggbbaa
        return hex;
    }

    // 1. HARVEST COLORS
    const colorCounts = new Map(); // hex -> { hex, rgb, roles: Set, count, sampleEl }
    function recordColor(rgbStr, role, elName) {
        const hex = rgbToHex(rgbStr);
        if (!hex) return;
        if (!colorCounts.has(hex)) {
            colorCounts.set(hex, {
                hex: hex,
                rgb: rgbStr,
                roles: new Set([role]),
                count: 1,
                sampleEl: elName
            });
        } else {
            const entry = colorCounts.get(hex);
            entry.roles.add(role);
            entry.count += 1;
        }
    }

    // Probing root and body
    const bodyStyle = window.getComputedStyle(document.body);
    recordColor(bodyStyle.backgroundColor, 'background', 'body');
    recordColor(bodyStyle.color, 'text', 'body');

    // Probing key landmarks
    const landmarks = document.querySelectorAll('header, nav, main, section, article, aside, footer');
    landmarks.forEach(lm => {
        const tag = lm.tagName.toLowerCase();
        const cs = window.getComputedStyle(lm);
        if (cs.backgroundColor && cs.backgroundColor !== 'rgba(0, 0, 0, 0)') {
            recordColor(cs.backgroundColor, 'background', tag);
        }
        if (cs.color) {
            recordColor(cs.color, 'text', tag);
        }
    });

    // Probing interactive buttons & cards for accents and surfaces
    const buttons = document.querySelectorAll('button, a[role="button"], input[type="submit"], .btn, [class*="button"]');
    buttons.forEach((btn, idx) => {
        if (idx > 25) return;
        const cs = window.getComputedStyle(btn);
        if (cs.backgroundColor && cs.backgroundColor !== 'rgba(0, 0, 0, 0)') {
            recordColor(cs.backgroundColor, 'accent', 'button');
        }
        if (cs.color) {
            recordColor(cs.color, 'text', 'button');
        }
    });

    const cards = document.querySelectorAll('[class*="card"], [class*="box"], [class*="tile"], article, section > div');
    cards.forEach((card, idx) => {
        if (idx > 25) return;
        const cs = window.getComputedStyle(card);
        if (cs.backgroundColor && cs.backgroundColor !== 'rgba(0, 0, 0, 0)') {
            recordColor(cs.backgroundColor, 'surface', 'card');
        }
    });

    const backgrounds = [];
    const surfaces = [];
    const textColors = [];
    const accents = [];
    const allColors = [];

    colorCounts.forEach(val => {
        const primaryRole = val.roles.has('accent') ? 'accent' :
                           val.roles.has('background') ? 'background' :
                           val.roles.has('surface') ? 'surface' :
                           val.roles.has('text') ? 'text' : 'unknown';

        const item = {
            hex: val.hex,
            rgb: val.rgb,
            role: primaryRole,
            frequency: val.count,
            source_element: val.sampleEl
        };
        allColors.push(item);

        if (val.roles.has('background') && !backgrounds.includes(val.hex)) backgrounds.push(val.hex);
        if (val.roles.has('surface') && !surfaces.includes(val.hex)) surfaces.push(val.hex);
        if (val.roles.has('text') && !textColors.includes(val.hex)) textColors.push(val.hex);
        if (val.roles.has('accent') && !accents.includes(val.hex)) accents.push(val.hex);
    });

    // 2. TYPOGRAPHY HARVESTING
    const rootStyle = window.getComputedStyle(document.documentElement);
    const primaryFont = bodyStyle.fontFamily || rootStyle.fontFamily || 'system-ui, sans-serif';
    const baseFontSize = bodyStyle.fontSize || '16px';
    const baseLineHeight = bodyStyle.lineHeight || '1.5';

    const headerScales = {};
    ['h1', 'h2', 'h3', 'h4'].forEach(tag => {
        const el = document.querySelector(tag);
        if (el) {
            const cs = window.getComputedStyle(el);
            headerScales[tag] = {
                tag: tag,
                font_family: cs.fontFamily,
                font_size: cs.fontSize,
                font_weight: cs.fontWeight,
                line_height: cs.lineHeight,
                letter_spacing: cs.letterSpacing !== 'normal' ? cs.letterSpacing : null
            };
        }
    });

    // 3. SPACING & ELEVATION
    const radiusCounts = new Map();
    const shadowCounts = new Map();

    const sampleElements = document.querySelectorAll('button, a[role="button"], input, select, textarea, [class*="card"], [class*="box"], [class*="modal"], [class*="dialog"], article');
    sampleElements.forEach((el, idx) => {
        if (idx > 50) return;
        const cs = window.getComputedStyle(el);
        const radius = cs.borderRadius;
        if (radius && radius !== '0px') {
            radiusCounts.set(radius, (radiusCounts.get(radius) || 0) + 1);
        }
        const shadow = cs.boxShadow;
        if (shadow && shadow !== 'none') {
            shadowCounts.set(shadow, (shadowCounts.get(shadow) || 0) + 1);
        }
    });

    const dominantRadii = Array.from(radiusCounts.entries())
        .sort((a, b) => b[1] - a[1])
        .slice(0, 5)
        .map(entry => entry[0]);

    const dominantShadows = Array.from(shadowCounts.entries())
        .sort((a, b) => b[1] - a[1])
        .slice(0, 5)
        .map(entry => entry[0]);

    // 4. CSS CUSTOM PROPERTIES
    const MAX_CUSTOM_PROPS = 150;
    const customProps = new Map();     // name -> value
    const setProps = new Map();        // name -> true when set by an unconditional plain root rule
    const PLAIN_ROOT_RE = /^(?:html|:root|:host|body)$/i;
    // html/:root/:host/body optionally qualified by attributes, classes, ids or :not/:is/:where
    const QUALIFIED_ROOT_RE = /^(?:html|:root|:host(?:\([^)]*\))?|body)?(?:\[[^\]]*\]|\.[\w-]+|#[\w-]+|:(?:not|is|where)\([^)]*\))*$/i;
    // Bare theme-attribute selectors such as [data-theme="dark"] or [data-bs-theme=light]
    const THEME_ATTR_RE = /^\[(?:data-[\w-]*theme[\w-]*|data-mode|data-color-scheme|theme)[^\]]*\]$/i;

    function splitSelectors(text) {
        const out = [];
        let depth = 0, quote = null, cur = '';
        for (const ch of text) {
            if (quote) { if (ch === quote) quote = null; cur += ch; continue; }
            if (ch === '"' || ch === "'") { quote = ch; cur += ch; continue; }
            if (ch === '(' || ch === '[') depth++;
            if (ch === ')' || ch === ']') depth--;
            if (ch === ',' && depth === 0) { out.push(cur.trim()); cur = ''; continue; }
            cur += ch;
        }
        if (cur.trim()) out.push(cur.trim());
        return out;
    }

    // Returns 'plain' | 'qualified' | null for a single selector
    function rootKind(sel) {
        if (PLAIN_ROOT_RE.test(sel)) return 'plain';
        if (THEME_ATTR_RE.test(sel)) return 'qualified';
        if (sel && QUALIFIED_ROOT_RE.test(sel) && /^(?:html|:root|:host|body)/i.test(sel)) return 'qualified';
        return null;
    }

    function recordProps(style, plain) {
        for (let i = 0; i < style.length; i++) {
            const name = style[i];
            if (!name.startsWith('--')) continue;
            const value = style.getPropertyValue(name).trim();
            if (plain) {
                customProps.set(name, value);
                setProps.set(name, true);
            } else if (!setProps.has(name)) {
                // Theme variants / conditional overrides must not clobber the default value
                if (!customProps.has(name)) customProps.set(name, value);
            }
        }
    }

    // Rule types: 1 = style rule; everything with cssRules (@layer, @media, @supports,
    // @container, nested style rules) is recursed into.
    function walkRules(rules, conditional, depth) {
        if (!rules || depth > 12) return;
        for (const rule of Array.from(rules)) {
            if (rule.type === 1 && rule.selectorText !== undefined) {
                const kinds = splitSelectors(rule.selectorText).map(rootKind).filter(Boolean);
                if (kinds.length) {
                    const plain = !conditional && kinds.includes('plain');
                    recordProps(rule.style, plain);
                }
            }
            if (rule.cssRules && rule.cssRules.length) {
                // @media / @supports / @container carry a condition; @layer does not
                const isConditional = conditional || !!rule.conditionText || !!rule.media;
                walkRules(rule.cssRules, isConditional, depth + 1);
            }
        }
    }

    let blockedSheet = false;
    for (const sheet of Array.from(document.styleSheets)) {
        let rules = null;
        try {
            rules = sheet.cssRules;
        } catch (e) {
            blockedSheet = true;  // cross-origin stylesheet
            continue;
        }
        try {
            walkRules(rules, false, 0);
        } catch (e) {}
    }

    // Inline style on documentElement (any custom property)
    recordProps(document.documentElement.style, true);

    // Cross-origin sheets cannot be read: Chromium lists custom properties when iterating the
    // computed style declaration, so use it for names we have not already seen.
    if (blockedSheet) {
        try {
            const computed = window.getComputedStyle(document.documentElement);
            for (let i = 0; i < computed.length; i++) {
                const name = computed[i];
                if (!name.startsWith('--') || customProps.has(name)) continue;
                const value = computed.getPropertyValue(name).trim();
                if (value) customProps.set(name, value);
            }
        } catch (e) {}
    }

    function propPriority(name) {
        const n = name.toLowerCase();
        if (/color|colour|primary|secondary|accent|theme|background|bg|foreground|surface|brand/.test(n)) return 0;
        if (/font|text|typography|leading|tracking/.test(n)) return 1;
        if (/radius|rounded/.test(n)) return 2;
        if (/shadow|elevation/.test(n)) return 3;
        if (/spacing|space|gap|padding|margin|size/.test(n)) return 4;
        return 5;
    }
    const sortedProps = Array.from(customProps.entries())
        .map((entry, idx) => ({ entry, idx, pri: propPriority(entry[0]) }))
        .sort((a, b) => a.pri - b.pri || a.idx - b.idx)
        .slice(0, MAX_CUSTOM_PROPS);
    const customPropsOut = {};
    sortedProps.forEach(p => { customPropsOut[p.entry[0]] = p.entry[1]; });

    return {
        colors: {
            backgrounds: backgrounds,
            surfaces: surfaces,
            text: textColors,
            accents: accents,
            all_colors: allColors
        },
        typography: {
            primary_font_family: primaryFont,
            secondary_font_family: null,
            base_font_size: baseFontSize,
            base_line_height: baseLineHeight,
            header_scales: headerScales
        },
        spacing_elevation: {
            dominant_border_radii: dominantRadii,
            box_shadows: dominantShadows
        },
        custom_properties: customPropsOut
    };
}
"""


def normalize_font_family(font_str: str) -> str:
    """Clean up and simplify font-family declaration string."""
    cleaned = re.sub(r'["\']', "", font_str).strip()
    return cleaned


def extract_tokens_from_dict(raw: dict[str, Any]) -> DesignTokens:
    """Convert raw dictionary extracted from client-side JS into typed DesignTokens model."""
    raw_colors = raw.get("colors", {})
    all_color_models: list[ColorToken] = []

    for c in raw_colors.get("all_colors", []):
        all_color_models.append(
            ColorToken(
                hex=c.get("hex", "#000000"),
                rgb=c.get("rgb", "rgb(0, 0, 0)"),
                role=c.get("role", "unknown"),
                frequency=c.get("frequency", 1),
                source_element=c.get("source_element"),
            )
        )

    palette = ColorPalette(
        backgrounds=raw_colors.get("backgrounds", []),
        surfaces=raw_colors.get("surfaces", []),
        text=raw_colors.get("text", []),
        accents=raw_colors.get("accents", []),
        all_colors=all_color_models,
    )

    raw_typo = raw.get("typography", {})
    raw_scales = raw_typo.get("header_scales", {})
    scales: dict[str, HeaderScale] = {}

    for tag, scale_data in raw_scales.items():
        scales[tag] = HeaderScale(
            tag=scale_data.get("tag", tag),
            font_family=normalize_font_family(scale_data.get("font_family", "")),
            font_size=scale_data.get("font_size", "16px"),
            font_weight=str(scale_data.get("font_weight", "400")),
            line_height=scale_data.get("line_height", "normal"),
            letter_spacing=scale_data.get("letter_spacing"),
        )

    typography = TypographyTokens(
        primary_font_family=normalize_font_family(
            raw_typo.get("primary_font_family", "system-ui, sans-serif")
        ),
        secondary_font_family=raw_typo.get("secondary_font_family"),
        base_font_size=raw_typo.get("base_font_size", "16px"),
        base_line_height=raw_typo.get("base_line_height", "1.5"),
        header_scales=scales,
    )

    raw_spacing = raw.get("spacing_elevation", {})
    spacing_elevation = SpacingElevationTokens(
        dominant_border_radii=raw_spacing.get("dominant_border_radii", []),
        box_shadows=raw_spacing.get("box_shadows", []),
    )

    custom_properties: dict[str, str] = raw.get("custom_properties", {})

    return DesignTokens(
        colors=palette,
        typography=typography,
        spacing_elevation=spacing_elevation,
        custom_properties=custom_properties,
    )


def harvest_design_tokens(page: Page) -> DesignTokens:
    """Execute client-side JS evaluation on Playwright page to harvest design tokens."""
    try:
        raw_result: Any = page.evaluate(HARVEST_TOKENS_JS)
        if isinstance(raw_result, dict):
            return extract_tokens_from_dict(raw_result)
        logger.warning("Token extraction JS returned unexpected type: %s", type(raw_result))
    except Exception as e:
        logger.warning("Failed to harvest design tokens via JS evaluation: %s", e)

    return DesignTokens()
