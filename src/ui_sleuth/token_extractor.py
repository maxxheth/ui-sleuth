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
HARVEST_TOKENS_JS = """
() => {
    const colorCanvas = document.createElement('canvas');
    colorCanvas.width = 1;
    colorCanvas.height = 1;
    let colorCtx = null;
    try { colorCtx = colorCanvas.getContext('2d'); } catch (e) {}

    function rgbToHex(rgbStr) {
        if (!rgbStr || rgbStr === 'transparent' || rgbStr === 'rgba(0, 0, 0, 0)') {
            return null;
        }
        if (rgbStr.startsWith('#')) {
            if (rgbStr.length === 4) {
                return '#' + rgbStr[1] + rgbStr[1] + rgbStr[2] + rgbStr[2] + rgbStr[3] + rgbStr[3];
            }
            return rgbStr.toLowerCase().slice(0, 7);
        }

        const match = rgbStr.match(/rgba?\\((\\d+)[,\\s]+(\\d+)[,\\s]+(\\d+)(?:[,/\\s]+([\\d.]+))?\\)/);
        if (match) {
            if (match[4] !== undefined && parseFloat(match[4]) < 0.1) {
                return null;
            }
            const r = parseInt(match[1], 10).toString(16).padStart(2, '0');
            const g = parseInt(match[2], 10).toString(16).padStart(2, '0');
            const b = parseInt(match[3], 10).toString(16).padStart(2, '0');
            return ('#' + r + g + b).toLowerCase();
        }

        // Support modern CSS color functions (oklch, lab, color(), hsl) by normalizing via 2D canvas context
        if (colorCtx) {
            try {
                colorCtx.fillStyle = '#000000';
                colorCtx.fillStyle = rgbStr;
                const normalized = colorCtx.fillStyle;
                if (normalized && normalized !== '#000000') {
                    if (normalized.startsWith('#')) return normalized.toLowerCase().slice(0, 7);
                    const nMatch = normalized.match(/rgba?\\((\\d+)[,\\s]+(\\d+)[,\\s]+(\\d+)(?:[,/\\s]+([\\d.]+))?\\)/);
                    if (nMatch) {
                        if (nMatch[4] !== undefined && parseFloat(nMatch[4]) < 0.1) return null;
                        const r = parseInt(nMatch[1], 10).toString(16).padStart(2, '0');
                        const g = parseInt(nMatch[2], 10).toString(16).padStart(2, '0');
                        const b = parseInt(nMatch[3], 10).toString(16).padStart(2, '0');
                        return ('#' + r + g + b).toLowerCase();
                    }
                }
            } catch (e) {}
        }
        return null;
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

    // 4. CSS CUSTOM PROPERTIES (--color-*, --theme-*)
    const customProps = {};
    try {
        for (const sheet of Array.from(document.styleSheets)) {
            try {
                const rules = sheet.cssRules || sheet.rules;
                if (!rules) continue;
                for (const rule of Array.from(rules)) {
                    if (rule.selectorText && (rule.selectorText === ':root' || rule.selectorText === 'html')) {
                        const style = rule.style;
                        for (let i = 0; i < style.length; i++) {
                            const propName = style[i];
                            if (propName.startsWith('--color-') || propName.startsWith('--theme-') || propName.startsWith('--primary') || propName.startsWith('--accent')) {
                                customProps[propName] = style.getPropertyValue(propName).trim();
                            }
                        }
                    }
                }
            } catch (e) {
                // Cross-origin stylesheet access restriction, ignore
            }
        }
    } catch (e) {}

    // Fallback: check inline style on documentElement
    const inlineStyle = document.documentElement.style;
    for (let i = 0; i < inlineStyle.length; i++) {
        const propName = inlineStyle[i];
        if (propName.startsWith('--color-') || propName.startsWith('--theme-')) {
            customProps[propName] = inlineStyle.getPropertyValue(propName).trim();
        }
    }

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
        custom_properties: customProps
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
