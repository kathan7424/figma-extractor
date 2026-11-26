#!/usr/bin/env python3
"""
figma2html - Figma Export Kit converter.

Reads the .zip (or extracted folder) made by the Figma Export Kit plugin and writes:
  manifest.json, tokens.json, tokens.css     design data + CSS variables
  pages/<page>/<frame>.json                  one JSON per frame (from the plugin)
  assets/images | svg | raster               original images, icons, raster fallbacks
  renders/<page>/<frame>.png                 Figma's own render (for visual checks)
  html/<page>/<frame>.html + .css            HTML/CSS for every frame
  html/<page>/<name>.responsive.html         desktop/tablet/mobile frames merged (if named so)
  elementor/<frame>.json                     Elementor templates (import in WordPress)
  index.html                                 gallery of everything

Free and offline: Python standard library only. No API keys, no network.
Usage:  python figma2html.py export.zip -o out_folder [--mode exact|responsive]
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import random
import re
import shutil
import string
import sys
import zipfile
from urllib.parse import quote
from pathlib import Path

MIXED = 'MIXED'
VERSION = '1.0.0'

# --------------------------------------------------------------------------- helpers


def fmt(v, p=2):
    r = round(float(v), p)
    if r == 0:
        return '0'
    s = f'{r:.{p}f}'
    return s.rstrip('0').rstrip('.') if '.' in s else s


def px(v):
    s = fmt(v)
    return '0' if s == '0' else s + 'px'


def num(v, default=0.0):
    return default if v is None or v == MIXED else float(v)


def slug(s, maxlen=40):
    s = re.sub(r'[^a-zA-Z0-9]+', '-', str(s or '')).strip('-').lower()
    s = s[:maxlen].strip('-')
    return s or 'node'


def esc_text(s):
    return html.escape(s, quote=False)


def esc_attr(s):
    return html.escape(str(s), quote=True)


def rgba(c, opacity=1.0):
    r = round(c.get('r', 0) * 255)
    g = round(c.get('g', 0) * 255)
    b = round(c.get('b', 0) * 255)
    a = c.get('a', 1) * opacity
    if a >= 0.999:
        return f'#{r:02x}{g:02x}{b:02x}'
    return f'rgba({r}, {g}, {b}, {fmt(a, 3)})'


def visible(paints):
    if not isinstance(paints, list):
        return []
    return [p for p in paints if p.get('visible', True) is not False and p.get('opacity', 1) > 0.001]


# 2x3 affine matrices in Figma layout: [[a, c, e], [b, d, f]]
IDENTITY = [[1, 0, 0], [0, 1, 0]]


def mat_inv(m):
    (a, c, e), (b, d, f) = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        return [row[:] for row in IDENTITY]
    ia, ic, ib, id_ = d / det, -c / det, -b / det, a / det
    return [[ia, ic, -(ia * e + ic * f)], [ib, id_, -(ib * e + id_ * f)]]


def mat_mul(m1, m2):
    (a1, c1, e1), (b1, d1, f1) = m1
    (a2, c2, e2), (b2, d2, f2) = m2
    return [[a1 * a2 + c1 * b2, a1 * c2 + c1 * d2, a1 * e2 + c1 * f2 + e1],
            [b1 * a2 + d1 * b2, b1 * c2 + d1 * d2, b1 * e2 + d1 * f2 + f1]]


def mat_apply(m, x, y):
    return (m[0][0] * x + m[0][1] * y + m[0][2], m[1][0] * x + m[1][1] * y + m[1][2])


BLEND = {
    'MULTIPLY': 'multiply', 'SCREEN': 'screen', 'OVERLAY': 'overlay', 'DARKEN': 'darken',
    'LIGHTEN': 'lighten', 'COLOR_DODGE': 'color-dodge', 'COLOR_BURN': 'color-burn',
    'HARD_LIGHT': 'hard-light', 'SOFT_LIGHT': 'soft-light', 'DIFFERENCE': 'difference',
    'EXCLUSION': 'exclusion', 'HUE': 'hue', 'SATURATION': 'saturation', 'COLOR': 'color',
    'LUMINOSITY': 'luminosity', 'LINEAR_BURN': 'multiply', 'LINEAR_DODGE': 'plus-lighter',
}
JUSTIFY = {'MIN': 'flex-start', 'CENTER': 'center', 'MAX': 'flex-end', 'SPACE_BETWEEN': 'space-between'}
ALIGN = {'MIN': 'flex-start', 'CENTER': 'center', 'MAX': 'flex-end', 'BASELINE': 'baseline'}
TEXT_ALIGN = {'LEFT': 'left', 'CENTER': 'center', 'RIGHT': 'right', 'JUSTIFIED': 'justify'}
STYLE_WEIGHTS = [
    ('thin', 100), ('hairline', 100), ('extralight', 200), ('extra light', 200), ('ultralight', 200),
    ('light', 300), ('regular', 400), ('normal', 400), ('book', 400), ('medium', 500),
    ('semibold', 600), ('semi bold', 600), ('demibold', 600), ('extrabold', 800), ('extra bold', 800),
    ('ultrabold', 800), ('bold', 700), ('black', 900), ('heavy', 900),
]
SYSTEM_FONTS = {
    'arial', 'helvetica', 'helvetica neue', 'times new roman', 'times', 'georgia', 'verdana',
    'courier new', 'tahoma', 'trebuchet ms', 'segoe ui', 'sf pro', 'sf pro display', 'sf pro text',
    'system-ui', '-apple-system', 'impact', 'comic sans ms',
}
RESET_VALUES = {
    'text-decoration': 'none', 'text-transform': 'none', 'font-style': 'normal',
    'font-variant': 'normal', 'letter-spacing': 'normal', 'background-image': 'none',
    '-webkit-background-clip': 'border-box', 'background-clip': 'border-box',
}


def weight_from_style(style):
    s = (style or '').lower()
    for key, w in STYLE_WEIGHTS:
        if key in s:
            return w
    return 400


BASE_CSS = """{s}*, {s}*::before, {s}*::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
{s}img {{ display: block; max-width: none; }}
{s}a {{ color: inherit; text-decoration: inherit; }}
{s}header, {s}nav, {s}section, {s}footer, {s}main, {s}article, {s}aside, {s}figure,
{s}h1, {s}h2, {s}h3, {s}h4, {s}h5, {s}h6, {s}p, {s}ul, {s}ol, {s}li {{ display: block; font: inherit; list-style: none; }}
{s}button {{ all: unset; display: block; box-sizing: border-box; cursor: pointer; }}"""

FIT_SCRIPT = """<script>
/* Figma Export Kit: scale fixed-width frames down to fit narrow screens */
(function () {
  function fit() {
    document.querySelectorAll('[data-fek-fit]').forEach(function (wrap) {
      var root = wrap.firstElementChild;
      if (!root) return;
      var w = parseFloat(wrap.getAttribute('data-fek-fit'));
      var avail = wrap.parentElement ? wrap.parentElement.clientWidth : window.innerWidth;
      var s = Math.min(1, avail / w);
      root.style.zoom = s < 0.999 ? s : '';
    });
  }
  window.addEventListener('resize', fit);
  if (document.readyState !== 'loading') fit(); else document.addEventListener('DOMContentLoaded', fit);
})();
</script>"""


# --------------------------------------------------------------------------- HTML generator


class HtmlGen:
    """Turns one frame JSON (from the plugin) into HTML + CSS."""

    def __init__(self, images, asset_prefix, mode='exact', scope='', semantic=True):
        self.semantic = semantic
        self.images = images or {}
        self.prefix = asset_prefix
        self.exact = mode == 'exact'
        self.scope = scope  # e.g. '.fek-abc ' to scope every rule (used for Elementor)
        self.rules = []
        self.fonts = {}
        self.warnings = []
        self.first_flow_child = {}
        self.overrides = {}  # node id -> asset dict (auto-fix: node replaced by a crop of Figma's render)

    # ---------- css bookkeeping
    def rule(self, selector, st):
        if st:
            self.rules.append((self.scope + selector, dict(st)))

    def css_text(self, include_base=True):
        out = []
        if include_base:
            out.append(BASE_CSS.format(s=self.scope))
        # identical declaration blocks share one rule: ".a, .b { ... }" (classes are unique per node, so order-safe)
        groups = {}
        for item in self.rules:
            if not item:
                continue
            sel, st = item
            body = '\n'.join(f'  {k}: {v};' for k, v in st.items())
            groups.setdefault(body, []).append(sel)
        for body, sels in groups.items():
            out.append(',\n'.join(sels) + f' {{\n{body}\n}}')
        return '\n\n'.join(out) + '\n'

    SECTION_TAGS = re.compile(r'^(header|nav|footer|main|section|aside|article)\b', re.I)
    BUTTON_NAME = re.compile(r'^(btn|button|cta)\b', re.I)

    def pick_tag(self, n, parent):
        """Semantic tag from the layer name (visual output is unchanged: BASE_CSS resets every tag)."""
        if not self.semantic:
            return 'div'
        name = (n.get('name') or '').strip()
        if n.get('type') == 'TEXT':
            m = re.match(r'^h([1-6])\b', name, re.I)
            if m:
                return 'h' + m.group(1)
            return 'h2' if re.match(r'^(heading|title)\b', name, re.I) else 'p'
        if parent is None:
            return 'div'
        m = self.SECTION_TAGS.match(name)
        if m:
            return m.group(1).lower()
        kids = [c for c in (n.get('children') or []) if c.get('visible') is not False]
        if self.BUTTON_NAME.match(name) and len(kids) == 1 and kids[0].get('type') == 'TEXT':
            return 'button'
        return 'div'

    def classname(self, n):
        idpart = re.sub(r'[^a-zA-Z0-9]+', '-', n.get('id', 'x')).strip('-')
        name = f"{slug(n.get('name'), 24)}-{idpart}"
        return name if name[0].isalpha() else 'n' + name

    def font_links(self, local=()):
        links = []
        for fam in sorted(self.fonts):
            if fam.lower() in SYSTEM_FONTS or fam in local:
                continue
            for weight, italic in sorted(self.fonts[fam]):
                spec = f"ital,wght@{1 if italic else 0},{weight}"
                links.append('https://fonts.googleapis.com/css2?family=' + fam.replace(' ', '+') + ':' + spec + '&display=swap')
        return links

    # ---------- entry
    def render_root(self, node):
        return self.render(node, None, 1)

    def render(self, n, parent, depth):
        if n.get('visible') is False or n.get('isMask'):
            return ''
        ov = self.overrides.get(n.get('id'))
        if ov and parent is not None:
            n = {k: v for k, v in n.items() if k != 'children'}
            n['asset'] = ov
            n['absoluteBoundingBox'] = ov['renderBounds']
        cls = self.classname(n)
        st = {}
        ind = '  ' * depth
        attrs = f' class="{cls}" data-name="{esc_attr(n.get("name", ""))}"'
        self.place(n, parent, st)

        if 'asset' in n:
            if n['asset'].get('cssVisuals'):  # asset drawn by us (not exported by Figma): add opacity/blend/effects
                self.asset_visuals(n, st)
            return self.render_asset(n, cls, st, ind, attrs)

        img = self.simple_image(n)
        layers = self.visuals(n, st, cls, skip_fills=bool(img))

        if n.get('type') == 'TEXT':
            inner = self.text(n, st, cls)
            self.rule('.' + cls, st)
            tag = self.pick_tag(n, parent)
            return f'{ind}<{tag}{attrs}>{inner}</{tag}>\n'

        if img:
            st['object-fit'] = img['fit']
            st['object-position'] = 'center'
            st['display'] = 'block'
            self.rule('.' + cls, st)
            return f'{ind}<img{attrs} src="{esc_attr(img["url"])}" alt="{esc_attr(n.get("name", ""))}" loading="lazy">\n'

        self.container(n, st)
        slot = len(self.rules)  # keep parent rule before its children in the CSS file
        self.rules.append(None)
        children = n.get('children') or []
        flow = [c for c in children if c.get('layoutPositioning') != 'ABSOLUTE' and c.get('visible') is not False]
        if flow:
            self.first_flow_child[n['id']] = flow[0]['id']
        inner = ''.join(self.render(c, n, depth + 1) for c in children)
        if layers:
            inner = ''.join(f'{ind}  {l}\n' for l in layers) + inner
        self.rules[slot] = (self.scope + '.' + cls, dict(st)) if st else None
        tag = self.pick_tag(n, parent)
        if not inner:
            return f'{ind}<{tag}{attrs}></{tag}>\n'
        return f'{ind}<{tag}{attrs}>\n{inner}{ind}</{tag}>\n'

    def asset_visuals(self, n, st):
        op = num(n.get('opacity'), 1)
        if op < 0.999:
            st['opacity'] = fmt(op, 3)
        blend = BLEND.get(n.get('blendMode') or '')
        if blend:
            st['mix-blend-mode'] = blend
        filters = []
        for e in reversed([e for e in (n.get('effects') or []) if e.get('visible', True)]):
            t = e.get('type')
            if t == 'DROP_SHADOW':
                o = e.get('offset') or {'x': 0, 'y': 0}
                filters.append(f"drop-shadow({px(o['x'])} {px(o['y'])} {px(num(e.get('radius')) / 2)} {rgba(e.get('color') or {'r': 0, 'g': 0, 'b': 0, 'a': 0.25})})")
            elif t == 'LAYER_BLUR':
                filters.append(f"blur({px(num(e.get('radius')) / 2)})")
        if filters:
            st['filter'] = ' '.join(filters)

    # ---------- positioning & sizing
    def node_box(self, n):
        if 'asset' in n and n.get('absoluteBoundingBox'):
            bb = n['absoluteBoundingBox']
            return bb['width'], bb['height']
        return num(n.get('width')), num(n.get('height'))

    def place(self, n, parent, st):
        W, H = self.node_box(n)
        if parent is None:
            st['position'] = 'relative'
            st['width'] = px(W)
            st['height' if self.exact else 'min-height'] = px(H)
            st['margin'] = '0 auto'
            st['flex-shrink'] = '0'
            return
        lm = parent.get('layoutMode')
        in_flow = lm in ('HORIZONTAL', 'VERTICAL') and n.get('layoutPositioning') != 'ABSOLUTE'
        if in_flow:
            st['position'] = 'relative'
            self.flow_size(n, parent, st, W, H)
            gap = num(parent.get('itemSpacing'))
            if gap < 0 and self.first_flow_child.get(parent['id']) != n['id'] \
                    and parent.get('primaryAxisAlignItems') != 'SPACE_BETWEEN':
                st['margin-left' if lm == 'HORIZONTAL' else 'margin-top'] = px(gap)
            return

        st['position'] = 'absolute'
        pT = parent.get('absoluteTransform') or IDENTITY
        if 'asset' in n and n.get('absoluteBoundingBox'):
            bb = n['absoluteBoundingBox']
            x, y = mat_apply(mat_inv(pT), bb['x'], bb['y'])
            st['left'] = px(x)
            st['top'] = px(y)
        else:
            rel = mat_mul(mat_inv(pT), n.get('absoluteTransform') or IDENTITY)
            (a, c, e), (b, d, f) = rel
            if abs(a - 1) < 1e-4 and abs(d - 1) < 1e-4 and abs(b) < 1e-4 and abs(c) < 1e-4:
                st['left'] = px(e)
                st['top'] = px(f)
            else:
                st['left'] = '0'
                st['top'] = '0'
                st['transform-origin'] = '0 0'
                st['transform'] = f'matrix({fmt(a, 5)}, {fmt(b, 5)}, {fmt(c, 5)}, {fmt(d, 5)}, {fmt(e)}, {fmt(f)})'
        st['width'] = px(W)
        st['height'] = px(H)
        if not self.exact and n.get('type') == 'TEXT' and n.get('textAutoResize') == 'WIDTH_AND_HEIGHT':
            st.pop('width', None)
            st.pop('height', None)

    def flow_size(self, n, parent, st, W, H):
        horiz = parent.get('layoutMode') == 'HORIZONTAL'
        sh = n.get('layoutSizingHorizontal') or 'FIXED'
        sv = n.get('layoutSizingVertical') or 'FIXED'
        prim, cross = (sh, sv) if horiz else (sv, sh)
        pprop, cprop = ('width', 'height') if horiz else ('height', 'width')
        pval, cval = (W, H) if horiz else (H, W)
        if self.exact:
            st[pprop] = px(pval)
            st[cprop] = px(cval)
            st['flex-shrink'] = '0'
            return
        if prim == 'FILL' or num(n.get('layoutGrow')) >= 1:
            st['flex'] = '1 1 0'
            st['min-' + pprop] = '0'
        elif prim == 'HUG':
            st['flex'] = '0 0 auto'
        else:
            st['flex'] = '0 0 auto'
            st[pprop] = px(pval)
        if cross == 'FILL' or n.get('layoutAlign') == 'STRETCH':
            st['align-self'] = 'stretch'
        elif cross == 'FIXED':
            st[cprop] = px(cval)
        for key, prop in (('minWidth', 'min-width'), ('maxWidth', 'max-width'),
                          ('minHeight', 'min-height'), ('maxHeight', 'max-height')):
            if isinstance(n.get(key), (int, float)):
                st[prop] = px(n[key])

    def container(self, n, st):
        lm = n.get('layoutMode')
        if lm not in ('HORIZONTAL', 'VERTICAL'):
            return
        row = lm == 'HORIZONTAL'
        st['display'] = 'flex'
        st['flex-direction'] = 'row' if row else 'column'
        pa = n.get('primaryAxisAlignItems') or 'MIN'
        ca = n.get('counterAxisAlignItems') or 'MIN'
        st['justify-content'] = JUSTIFY.get(pa, 'flex-start')
        st['align-items'] = ALIGN.get(ca, 'flex-start')
        gap = num(n.get('itemSpacing'))
        if pa != 'SPACE_BETWEEN' and gap > 0:
            st['column-gap' if row else 'row-gap'] = px(gap)
        if n.get('layoutWrap') == 'WRAP':
            st['flex-wrap'] = 'wrap'
            cg = num(n.get('counterAxisSpacing'))
            if cg > 0:
                st['row-gap' if row else 'column-gap'] = px(cg)
            st['align-content'] = 'space-between' if n.get('counterAxisAlignContent') == 'SPACE_BETWEEN' else 'flex-start'
        pads = [num(n.get(k)) for k in ('paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft')]
        if any(pads):
            st['padding'] = ' '.join(px(p) for p in pads)

    # ---------- assets
    def render_asset(self, n, cls, st, ind, attrs):
        a = n['asset']
        url = self.prefix + a['path']
        bb = n.get('absoluteBoundingBox') or a.get('renderBounds')
        rb = a.get('renderBounds') or bb
        alt = esc_attr(n.get('name', ''))
        if bb is None:
            self.rule('.' + cls, st)
            return f'{ind}<img{attrs} src="{esc_attr(url)}" alt="{alt}">\n'
        dx, dy = rb['x'] - bb['x'], rb['y'] - bb['y']
        same = abs(dx) < 0.01 and abs(dy) < 0.01 and abs(rb['width'] - bb['width']) < 0.01 and abs(rb['height'] - bb['height']) < 0.01
        if same:
            st['display'] = 'block'
            self.rule('.' + cls, st)
            return f'{ind}<img{attrs} src="{esc_attr(url)}" alt="{alt}">\n'
        self.rule('.' + cls, st)
        self.rule(f'.{cls} > img', {
            'position': 'absolute', 'left': px(dx), 'top': px(dy),
            'width': px(rb['width']), 'height': px(rb['height']), 'max-width': 'none',
        })
        return f'{ind}<div{attrs}><img src="{esc_attr(url)}" alt="{alt}"></div>\n'

    def image_url(self, paint):
        info = self.images.get(paint.get('imageHash') or '')
        if not info:
            self.warnings.append(f"Missing image {paint.get('imageHash')}")
            return None, None
        return self.prefix + info['path'], info

    def simple_image(self, n):
        if n.get('type') not in ('RECTANGLE', 'FRAME') or n.get('children'):
            return None
        fills = visible(n.get('fills'))
        if len(fills) != 1:
            return None
        p = fills[0]
        if p.get('type') != 'IMAGE' or p.get('scaleMode', 'FILL') not in ('FILL', 'FIT'):
            return None
        if p.get('opacity', 1) < 0.999 or p.get('blendMode', 'NORMAL') not in ('NORMAL', 'PASS_THROUGH'):
            return None
        if p.get('filters') and any(abs(v) > 0.001 for v in p['filters'].values()):
            return None
        url, _ = self.image_url(p)
        if not url:
            return None
        return {'url': url, 'fit': 'cover' if p.get('scaleMode', 'FILL') == 'FILL' else 'contain'}

    # ---------- paints
    def gradient_css(self, p, W, H, opacity=1.0):
        t = p.get('gradientTransform') or IDENTITY
        inv = mat_inv(t)
        stops = p.get('gradientStops') or []
        W = W or 1
        H = H or 1

        def pt(x, y):
            gx, gy = mat_apply(inv, x, y)
            return gx * W, gy * H

        typ = p['type']
        if typ == 'GRADIENT_LINEAR':
            x0, y0 = pt(0, 0.5)
            x1, y1 = pt(1, 0.5)
            dx, dy = x1 - x0, y1 - y0
            if abs(dx) < 1e-9 and abs(dy) < 1e-9:
                dx, dy = 0, 1
            ang = math.atan2(dx, -dy)
            ux, uy = math.sin(ang), -math.cos(ang)
            L = abs(W * math.sin(ang)) + abs(H * math.cos(ang)) or 1
            sx, sy = W / 2 - ux * L / 2, H / 2 - uy * L / 2
            parts = []
            for s in stops:
                qx, qy = x0 + dx * s['position'], y0 + dy * s['position']
                pct = ((qx - sx) * ux + (qy - sy) * uy) / L * 100
                parts.append(f"{rgba(s['color'], opacity)} {fmt(pct)}%")
            return f"linear-gradient({fmt(math.degrees(ang))}deg, {', '.join(parts)})"
        cx, cy = pt(0.5, 0.5)
        ax, ay = pt(1, 0.5)
        bx, by = pt(0.5, 1)
        stop_list = ', '.join(f"{rgba(s['color'], opacity)} {fmt(s['position'] * 100)}%" for s in stops)
        if typ == 'GRADIENT_ANGULAR':
            ang = math.degrees(math.atan2(ax - cx, -(ay - cy)))
            return f'conic-gradient(from {fmt(ang)}deg at {px(cx)} {px(cy)}, {stop_list})'
        rx = math.hypot(ax - cx, ay - cy) or 1
        ry = math.hypot(bx - cx, by - cy) or 1
        return f'radial-gradient(ellipse {px(rx)} {px(ry)} at {px(cx)} {px(cy)}, {stop_list})'

    def paint_layer(self, p, W, H, bake_opacity=True):
        t = p.get('type')
        op = p.get('opacity', 1) if bake_opacity else 1
        blend = BLEND.get(p.get('blendMode', 'NORMAL'), 'normal')
        if t == 'SOLID':
            c = rgba(p['color'], op)
            return {'kind': 'solid', 'color': c, 'image': f'linear-gradient({c}, {c})',
                    'size': 'auto', 'pos': '0 0', 'repeat': 'repeat', 'blend': blend}
        if t and t.startswith('GRADIENT'):
            return {'kind': 'gradient', 'image': self.gradient_css(p, W, H, op),
                    'size': 'auto', 'pos': '0 0', 'repeat': 'no-repeat', 'blend': blend}
        if t == 'IMAGE':
            url, info = self.image_url(p)
            if not url:
                return None
            mode = p.get('scaleMode', 'FILL')
            layer = {'kind': 'image', 'image': f'url("{url}")', 'blend': blend, 'opacity': p.get('opacity', 1)}
            if mode == 'FIT':
                layer.update(size='contain', pos='center', repeat='no-repeat')
            elif mode == 'TILE':
                sf = p.get('scalingFactor') or 1
                layer.update(size=f"{px(info['width'] * sf)} {px(info['height'] * sf)}", pos='0 0', repeat='repeat')
            elif mode == 'CROP' and p.get('imageTransform'):
                inv = mat_inv(p['imageTransform'])
                layer.update(size=f'{px(inv[0][0] * W)} {px(inv[1][1] * H)}',
                             pos=f'{px(inv[0][2] * W)} {px(inv[1][2] * H)}', repeat='no-repeat')
            else:
                layer.update(size='cover', pos='center', repeat='no-repeat')
            return layer
        return None

    def visuals(self, n, st, cls, skip_fills=False):
        typ = n.get('type')
        W, H = num(n.get('width')), num(n.get('height'))
        layers_html = []
        if n.get('maskAsset'):  # Figma mask: an SVG alpha mask clips the group's content
            # inline data URI: Chrome refuses external mask images on file:// pages (and a failed mask hides the content)
            src = ('data:image/svg+xml,' + quote(n['maskData'])) if n.get('maskData') else self.prefix + n['maskAsset']
            m = f'url("{src}") 0 0 / 100% 100% no-repeat'
            st['-webkit-mask'] = m
            st['mask'] = m
        if typ != 'TEXT' and not skip_fills:
            layers_html = self.fills(n, st, cls, W, H)

        if typ == 'ELLIPSE':
            st['border-radius'] = '50%'
        else:
            cr = n.get('cornerRadius')
            if cr == MIXED or cr is None:
                radii = [num(n.get(k)) for k in ('topLeftRadius', 'topRightRadius', 'bottomRightRadius', 'bottomLeftRadius')]
                if any(radii):
                    st['border-radius'] = ' '.join(px(r) for r in radii)
            elif num(cr) > 0:
                st['border-radius'] = px(cr)

        self.strokes(n, st, cls, W, H)
        self.effects(n, st)
        op = num(n.get('opacity'), 1)
        if op < 0.999:
            st['opacity'] = fmt(op, 3)
        blend = BLEND.get(n.get('blendMode') or '')
        if blend:
            st['mix-blend-mode'] = blend
        if n.get('clipsContent'):
            st['overflow'] = 'hidden'
        return layers_html

    def fills(self, n, st, cls, W, H):
        paints = visible(n.get('fills'))
        if not paints:
            return []
        need_layers = any(p.get('type') == 'IMAGE' and p.get('opacity', 1) < 0.999 for p in paints)
        if need_layers:
            out = []
            for i, p in enumerate(paints):
                L = self.paint_layer(p, W, H, bake_opacity=p.get('type') != 'IMAGE')
                if not L:
                    continue
                lc = f'{cls}-fill{i}'
                lst = {'position': 'absolute', 'inset': '0', 'border-radius': 'inherit', 'pointer-events': 'none',
                       'background-image': L['image'], 'background-size': L['size'],
                       'background-position': L['pos'], 'background-repeat': L['repeat']}
                if L.get('opacity', 1) < 0.999:
                    lst['opacity'] = fmt(L['opacity'], 3)
                if L['blend'] != 'normal':
                    lst['mix-blend-mode'] = L['blend']
                self.rule('.' + lc, lst)
                out.append(f'<div class="{lc}" aria-hidden="true"></div>')
            return out

        layers = [L for L in (self.paint_layer(p, W, H) for p in paints) if L]
        if not layers:
            return []
        top_first = list(reversed(layers))
        if top_first[-1]['kind'] == 'solid' and top_first[-1]['blend'] == 'normal':
            st['background-color'] = top_first.pop()['color']
        if top_first:
            st['background-image'] = ', '.join(L['image'] for L in top_first)
            if any(L['size'] != 'auto' for L in top_first):
                st['background-size'] = ', '.join(L['size'] for L in top_first)
            if any(L['pos'] != '0 0' for L in top_first):
                st['background-position'] = ', '.join(L['pos'] for L in top_first)
            st['background-repeat'] = ', '.join(L['repeat'] for L in top_first)
            if any(L['blend'] != 'normal' for L in top_first):
                st['background-blend-mode'] = ', '.join(L['blend'] for L in top_first)
        return []

    def strokes(self, n, st, cls, W, H):
        paints = visible(n.get('strokes'))
        if not paints:
            return
        p = paints[-1]
        sw = n.get('strokeWeight', 1)
        align = n.get('strokeAlign') or 'INSIDE'
        if n.get('type') == 'TEXT':
            if p.get('type') == 'SOLID':
                st['-webkit-text-stroke'] = f"{px(num(sw, 1))} {rgba(p['color'], p.get('opacity', 1))}"
                if align == 'OUTSIDE':
                    st['paint-order'] = 'stroke fill'
            return
        sides = [n.get(k, sw) for k in ('strokeTopWeight', 'strokeRightWeight', 'strokeBottomWeight', 'strokeLeftWeight')]
        sides = [num(v, num(sw, 1)) for v in sides]
        uniform = sw != MIXED and all(abs(s - sides[0]) < 0.001 for s in sides)
        style = 'dashed' if n.get('dashPattern') else 'solid'
        factor = {'INSIDE': 0.0, 'CENTER': 0.5, 'OUTSIDE': 1.0}.get(align, 0.0)
        if uniform and p.get('type') == 'SOLID':
            w = sides[0]
            if w <= 0:
                return
            st['outline'] = f"{px(w)} {style} {rgba(p['color'], p.get('opacity', 1))}"
            st['outline-offset'] = px(-w * (1 - factor))
            return
        t, r, b, l = sides
        after = {'content': '""', 'position': 'absolute', 'pointer-events': 'none',
                 'top': px(-t * factor), 'right': px(-r * factor), 'bottom': px(-b * factor), 'left': px(-l * factor),
                 'border-radius': 'inherit'}
        widths = f'{px(t)} {px(r)} {px(b)} {px(l)}'
        if p.get('type') == 'SOLID':
            after['border-style'] = style
            after['border-color'] = rgba(p['color'], p.get('opacity', 1))
            after['border-width'] = widths
        elif p.get('type', '').startswith('GRADIENT'):
            after['padding'] = widths
            after['background'] = self.gradient_css(p, W + (l + r) * factor, H + (t + b) * factor, p.get('opacity', 1))
            after['-webkit-mask'] = 'linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0)'
            after['-webkit-mask-composite'] = 'xor'
            after['mask'] = 'linear-gradient(#000 0 0) content-box exclude, linear-gradient(#000 0 0)'
        else:
            return
        self.rule(f'.{cls}::after', after)

    def effects(self, n, st):
        effs = [e for e in (n.get('effects') or []) if e.get('visible', True)]
        if not effs:
            return
        is_text = n.get('type') == 'TEXT'
        has_fill = bool(visible(n.get('fills')))
        shadows, text_shadows, filters, backdrop = [], [], [], []
        for e in reversed(effs):
            t = e.get('type')
            if t in ('DROP_SHADOW', 'INNER_SHADOW'):
                o = e.get('offset') or {'x': 0, 'y': 0}
                r = num(e.get('radius'))
                s = num(e.get('spread'))
                c = rgba(e.get('color') or {'r': 0, 'g': 0, 'b': 0, 'a': 0.25})
                if is_text:
                    if t == 'DROP_SHADOW':
                        text_shadows.append(f"{px(o['x'])} {px(o['y'])} {px(r)} {c}")
                elif t == 'DROP_SHADOW' and not has_fill and n.get('children'):
                    filters.append(f"drop-shadow({px(o['x'])} {px(o['y'])} {px(r / 2)} {c})")
                else:
                    shadows.append(('inset ' if t == 'INNER_SHADOW' else '') + f"{px(o['x'])} {px(o['y'])} {px(r)} {px(s)} {c}")
            elif t == 'LAYER_BLUR':
                filters.append(f"blur({px(num(e.get('radius')) / 2)})")
            elif t == 'BACKGROUND_BLUR':
                backdrop.append(f"blur({px(num(e.get('radius')) / 2)})")
        if shadows:
            st['box-shadow'] = ', '.join(shadows)
        if text_shadows:
            st['text-shadow'] = ', '.join(text_shadows)
        if filters:
            st['filter'] = ' '.join(filters)
        if backdrop:
            st['backdrop-filter'] = ' '.join(backdrop)
            st['-webkit-backdrop-filter'] = ' '.join(backdrop)

    # ---------- text
    def seg_css(self, seg, n):
        css = {}
        fn = seg.get('fontName') or {'family': 'Inter', 'style': 'Regular'}
        if fn == MIXED:
            fn = {'family': 'Inter', 'style': 'Regular'}
        family = fn.get('family', 'Inter')
        style = fn.get('style', 'Regular')
        weight = seg.get('fontWeight')
        if not isinstance(weight, (int, float)):
            weight = weight_from_style(style)
        italic = 'italic' in style.lower() or 'oblique' in style.lower()
        self.fonts.setdefault(family, set()).add((int(weight), italic))
        css['font-family'] = f'"{family}", system-ui, sans-serif'
        if isinstance(seg.get('fontSize'), (int, float)):
            css['font-size'] = px(seg['fontSize'])
        css['font-weight'] = str(int(weight))
        if italic:
            css['font-style'] = 'italic'
        lh = seg.get('lineHeight')
        if isinstance(lh, dict):
            if lh.get('unit') == 'PIXELS':
                css['line-height'] = px(lh['value'])
            elif lh.get('unit') == 'PERCENT':
                css['line-height'] = fmt(lh['value'] / 100, 4)
            else:
                css['line-height'] = 'normal'
        ls = seg.get('letterSpacing')
        if isinstance(ls, dict) and ls.get('value'):
            css['letter-spacing'] = px(ls['value']) if ls.get('unit') == 'PIXELS' else fmt(ls['value'] / 100, 4) + 'em'
        deco = seg.get('textDecoration')
        if deco == 'UNDERLINE':
            css['text-decoration'] = 'underline'
        elif deco == 'STRIKETHROUGH':
            css['text-decoration'] = 'line-through'
        case = seg.get('textCase')
        if case == 'UPPER':
            css['text-transform'] = 'uppercase'
        elif case == 'LOWER':
            css['text-transform'] = 'lowercase'
        elif case == 'TITLE':
            css['text-transform'] = 'capitalize'
        elif case == 'SMALL_CAPS':
            css['font-variant'] = 'small-caps'
        elif case == 'SMALL_CAPS_FORCED':
            css['font-variant'] = 'all-small-caps'
        fills = visible(seg.get('fills') if 'fills' in seg else n.get('fills'))
        if fills:
            p = fills[-1]
            if p.get('type') == 'SOLID':
                css['color'] = rgba(p['color'], p.get('opacity', 1))
            elif p.get('type', '').startswith('GRADIENT'):
                css['background-image'] = self.gradient_css(p, num(n.get('width')), num(n.get('height')), p.get('opacity', 1))
                css['-webkit-background-clip'] = 'text'
                css['background-clip'] = 'text'
                css['color'] = 'transparent'
        else:
            css['color'] = 'transparent'
        return css

    def text(self, n, st, cls):
        segs = n.get('segments') or [{'characters': n.get('characters', ''), 'fontName': {'family': 'Inter', 'style': 'Regular'}}]
        base = max(segs, key=lambda s: len(s.get('characters', '')))
        base_css = self.seg_css(base, n)
        st.update(base_css)
        st['text-align'] = TEXT_ALIGN.get(n.get('textAlignHorizontal') or 'LEFT', 'left')
        ar = n.get('textAutoResize') or 'NONE'
        st['white-space'] = 'pre' if ar == 'WIDTH_AND_HEIGHT' else 'pre-wrap'
        if ar != 'WIDTH_AND_HEIGHT':
            st['overflow-wrap'] = 'break-word'
        if n.get('textTruncation') == 'ENDING':
            st['overflow'] = 'hidden'
            st['text-overflow'] = 'ellipsis'
            if isinstance(n.get('maxLines'), int) and n['maxLines'] > 1:
                st['display'] = '-webkit-box'
                st['-webkit-box-orient'] = 'vertical'
                st['-webkit-line-clamp'] = str(n['maxLines'])

        parts = []
        for i, s in enumerate(segs):
            raw = (s.get('characters') or '').replace(' ', '\n').replace(' ', '\n')
            txt = esc_text(raw)
            css = self.seg_css(s, n)
            diff = {k: v for k, v in css.items() if base_css.get(k) != v}
            for k in base_css:
                if k not in css and k in RESET_VALUES:
                    diff[k] = RESET_VALUES[k]
            if diff:
                sc = f'{cls}-s{i}'
                self.rule('.' + sc, diff)
                txt = f'<span class="{sc}">{txt}</span>'
            link = s.get('hyperlink')
            if isinstance(link, dict) and link.get('type') == 'URL' and link.get('value'):
                txt = f'<a href="{esc_attr(link["value"])}">{txt}</a>'
            parts.append(txt)
        content = ''.join(parts)

        av = n.get('textAlignVertical') or 'TOP'
        fixed_h = ar in ('NONE', 'TRUNCATE') or self.exact
        if av in ('CENTER', 'BOTTOM') and fixed_h and st.get('display') != '-webkit-box':
            st['display'] = 'flex'
            st['flex-direction'] = 'column'
            st['justify-content'] = 'center' if av == 'CENTER' else 'flex-end'
            content = f'<span>{content}</span>'
        return content


# --------------------------------------------------------------------------- pages


def page_html(title, body, css_href=None, inline_css=None, font_links=(), fit_width=None, extra_head=''):
    head = [
        '<!doctype html>',
        '<html lang="en">',
        '<head>',
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f'<title>{esc_text(title)}</title>',
    ]
    if font_links:
        head.append('<link rel="preconnect" href="https://fonts.googleapis.com">')
        head.append('<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>')
        head.extend(f'<link rel="stylesheet" href="{esc_attr(u)}">' for u in font_links)
    if css_href:
        head.append(f'<link rel="stylesheet" href="{esc_attr(css_href)}">')
    if inline_css:
        head.append(f'<style>\n{inline_css}</style>')
    head.append('<style>body { margin: 0; background: #ffffff; -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; text-rendering: geometricPrecision; overflow-x: hidden; }</style>')
    if extra_head:
        head.append(extra_head)
    head.append('</head>')
    return '\n'.join(head) + '\n<body>\n' + body + (FIT_SCRIPT + '\n' if fit_width else '') + '</body>\n</html>\n'


def wrap_fit(inner_html, width):
    return f'<div class="fek-fit" data-fek-fit="{fmt(width)}">\n{inner_html}</div>\n'


BREAKPOINT_RE = re.compile(r'^(.*?)[\s_\-/|]*\(?\b(desktop|tablet|mobile|phone)\b\)?\s*$', re.I)


def breakpoint_of(name):
    m = BREAKPOINT_RE.match(name or '')
    if not m or not m.group(1).strip():
        return None, None
    kind = m.group(2).lower()
    return m.group(1).strip(), 'mobile' if kind == 'phone' else kind


def responsive_rules(kinds):
    """media queries that show exactly one variant per screen size"""
    order = [k for k in ('mobile', 'tablet', 'desktop') if k in kinds]
    bounds = {'mobile': (None, 767), 'tablet': (768, 1024), 'desktop': (1025, None)}
    rules = []
    for i, k in enumerate(order):
        lo = None if i == 0 else bounds[k][0]
        hi = None if i == len(order) - 1 else bounds[order[i + 1]][0] - 1
        cond = []
        if lo:
            cond.append(f'(min-width: {lo}px)')
        if hi:
            cond.append(f'(max-width: {hi}px)')
        rules.append(f'.fek-bp-{k} {{ display: none; }}')
        rules.append(f"@media {' and '.join(cond) or 'all'} {{ .fek-bp-{k} {{ display: block; }} }}")
    return '\n'.join(rules)


# --------------------------------------------------------------------------- Elementor


def el_id():
    return ''.join(random.choices(string.hexdigits.lower()[:16], k=7))


def elementor_template(title, widget_html):
    """Elementor JSON template: Section > Column > HTML widget (works with or without Flexbox Containers)."""
    zero = {'unit': 'px', 'top': '0', 'right': '0', 'bottom': '0', 'left': '0', 'isLinked': True}
    return {
        'version': '0.4',
        'title': title,
        'type': 'page',
        'page_settings': {'template': 'elementor_canvas'},
        'content': [{
            'id': el_id(),
            'elType': 'section',
            'isInner': False,
            'settings': {'layout': 'full_width', 'gap': 'no', 'padding': zero, 'margin': zero},
            'elements': [{
                'id': el_id(),
                'elType': 'column',
                'isInner': False,
                'settings': {'_column_size': 100, '_inline_size': None, 'padding': zero},
                'elements': [{
                    'id': el_id(),
                    'elType': 'widget',
                    'widgetType': 'html',
                    'isInner': False,
                    'settings': {'html': widget_html},
                    'elements': [],
                }],
            }],
        }],
    }


def elementor_widget_html(variants, images, asset_base, mode, overrides=None, local_fonts=()):
    """variants: list of (kind or None, node). overrides: {node id: asset} for a single variant,
    or {kind: {node id: asset}} for responsive variants. Returns scoped HTML+CSS for one HTML widget."""
    scope_id = 'fek-' + el_id()
    css_parts, body_parts, fonts = [], [], {}
    kinds = []
    overrides = overrides or {}
    for i, (kind, node) in enumerate(variants):
        gen = HtmlGen(images, asset_base, mode=mode, scope=f'.{scope_id} ')
        gen.overrides = overrides.get(kind, {}) if kind else overrides
        body = gen.render_root(node)
        css_parts.append(gen.css_text(include_base=(i == 0)))
        for f, v in gen.fonts.items():
            fonts.setdefault(f, set()).update(v)
        fit = wrap_fit(body, num(node.get('width')))
        if kind:
            kinds.append(kind)
            body_parts.append(f'<div class="fek-bp-{kind}">\n{fit}</div>\n')
        else:
            body_parts.append(fit)
    links_gen = HtmlGen({}, '')
    links_gen.fonts = fonts
    urls = links_gen.font_links(set(local_fonts))
    if set(local_fonts) & set(fonts):
        urls.append(asset_base + 'fonts/fonts.css')
    links = ''.join(f'<link rel="stylesheet" href="{esc_attr(u)}">\n' for u in urls)
    css = ''.join(css_parts)
    css += f'.{scope_id} {{ width: 100%; overflow-x: hidden; -webkit-font-smoothing: antialiased; }}\n'
    if kinds:
        css += responsive_rules(kinds).replace('.fek-bp-', f'.{scope_id} .fek-bp-') + '\n'
    return f'{links}<style>\n{css}</style>\n<div class="{scope_id}">\n{"".join(body_parts)}</div>\n{FIT_SCRIPT}\n'



# --------------------------------------------------------------------------- handoff brief (for Claude / developers)


def handoff_md(fr, node, images, max_depth=4, max_lines=700):
    """Compact Markdown outline of one frame: sizes, layout, colours, fonts, texts, images. Meant to be read by a
    person or by Claude when rebuilding the design in another tool (e.g. Elementor)."""
    fonts, colors, lines = {}, {}, []
    rb = node.get('absoluteBoundingBox') or {'x': 0, 'y': 0}

    def hexc(p):
        if p.get('type') != 'SOLID':
            return None
        c = rgba(p['color'], p.get('opacity', 1))
        colors[c] = colors.get(c, 0) + 1
        return c

    def describe(n):
        bits = []
        w, h = n.get('width'), n.get('height')
        if w is not None:
            bits.append(f'{fmt(w, 0)}x{fmt(h, 0)}')
        bb = n.get('absoluteBoundingBox')
        if bb:
            bits.append(f'@{fmt(bb["x"] - rb["x"], 0)},{fmt(bb["y"] - rb["y"], 0)}')
        if n.get('layoutMode'):
            pad = '/'.join(fmt(n.get(k, 0), 0) for k in ('paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft'))
            bits.append(f'auto-layout {n["layoutMode"].lower()} gap {fmt(n.get("itemSpacing", 0), 0)} pad {pad}')
        fills = [x for x in (hexc(p) for p in visible(n.get('fills'))) if x]
        if fills and n.get('type') != 'TEXT':
            bits.append('bg ' + '+'.join(fills))
        if any(p.get('type', '').startswith('GRADIENT') for p in visible(n.get('fills'))):
            bits.append('gradient')
        cr = n.get('cornerRadius')
        if cr and cr != MIXED:
            bits.append(f'radius {fmt(cr, 0)}')
        if visible(n.get('strokes')):
            bits.append(f'stroke {fmt(n.get("strokeWeight", 1), 0)}')
        if n.get('effects'):
            bits.append('effects ' + ','.join(sorted({e["type"].lower() for e in n['effects']})))
        if num(n.get('opacity'), 1) < 0.999:
            bits.append(f'opacity {fmt(n["opacity"], 2)}')
        return ' · '.join(bits)

    def walk(n, d):
        if len(lines) >= max_lines or n.get('visible') is False or n.get('isMask'):
            return
        ind = '  ' * d
        t = n.get('type')
        if t == 'TEXT':
            segs = n.get('segments') or []
            s = max(segs, key=lambda x: len(x.get('characters', ''))) if segs else {}
            fn = s.get('fontName') or {}
            key = f'{fn.get("family", "?")} {fn.get("style", "")}'.strip()
            fonts[key] = fonts.get(key, 0) + 1
            fills = visible(s.get('fills'))
            col = hexc(fills[-1]) if fills else None
            lh = s.get('lineHeight') or {}
            lhs = f'/{fmt(lh["value"], 0)}px' if lh.get('unit') == 'PIXELS' else ''
            txt = re.sub(r'[\n\u2028\u2029]+', ' ⏎ ', n.get('characters') or '')
            lines.append(f'{ind}- TEXT "{txt[:80]}{"…" if len(txt) > 80 else ""}" — {key} {fmt(s.get("fontSize", 0), 0)}px{lhs} '
                         f'{col or ""} {n.get("textAlignHorizontal", "").lower()} · {describe(n)}')
            return
        if 'asset' in n:
            a = n['asset']
            lines.append(f'{ind}- {a["format"].upper()} "{n.get("name", "")}" `{a["path"]}` · {describe(n)}')
            return
        img = next((p for p in visible(n.get('fills')) if p.get('type') == 'IMAGE'), None)
        label = f'{t} "{n.get("name", "")}"' + (f' (instance of {n["componentName"]})' if n.get('componentName') else '')
        extra = ''
        if img:
            info = images.get(img.get('imageHash') or '') or {}
            extra = f' · IMAGE `{info.get("path", "?")}` {img.get("scaleMode", "")}'
        lines.append(f'{ind}- {label} — {describe(n)}{extra}')
        if d < max_depth:
            for c in n.get('children') or []:
                walk(c, d + 1)
        elif n.get('children'):
            lines.append(f'{ind}  - … {len(n["children"])} children (see JSON)')

    walk(node, 0)
    head = [f'# {fr.get("name", "")}', '',
            f'- Page: {fr.get("page", "")} · size {fmt(fr.get("width", 0), 0)}x{fmt(fr.get("height", 0), 0)}',
            f'- JSON: `{fr.get("json", "")}`' + (f' · Figma render: `{fr["render"]}`' if fr.get('render') else '')]
    if colors:
        head.append('- Colours: ' + ', '.join(f'`{c}` ×{k}' for c, k in sorted(colors.items(), key=lambda x: -x[1])[:14]))
    if fonts:
        head.append('- Fonts: ' + ', '.join(f'{f} ×{k}' for f, k in sorted(fonts.items(), key=lambda x: -x[1])[:8]))
    note = ['', f'_Outline truncated at {max_lines} lines._'] if len(lines) >= max_lines else []
    return '\n'.join(head + ['', '## Structure (x,y relative to the frame)', ''] + lines + note) + '\n'


# --------------------------------------------------------------------------- tokens


def tokens_css(tokens):
    lines = [':root {']
    seen = set()

    def add(name, value):
        var = '--' + slug(name, 60)
        if var in seen or value is None:
            return
        seen.add(var)
        lines.append(f'  {var}: {value};')

    cols = {c['id']: c for c in tokens.get('variableCollections', [])}
    by_id = {v['id']: v for v in tokens.get('variables', [])}
    for v in tokens.get('variables', []):
        col = cols.get(v.get('collectionId'), {})
        mode = col.get('defaultModeId')
        val = (v.get('valuesByMode') or {}).get(mode)
        if isinstance(val, dict) and val.get('type') == 'VARIABLE_ALIAS':
            ref = by_id.get(val.get('id'))
            out = f"var(--{slug(ref['name'], 60)})" if ref else None
        elif v.get('resolvedType') == 'COLOR' and isinstance(val, dict):
            out = rgba(val)
        elif v.get('resolvedType') == 'FLOAT' and isinstance(val, (int, float)):
            out = fmt(val, 3) if 'opacity' in v['name'].lower() or 'weight' in v['name'].lower() else px(val)
        elif isinstance(val, str):
            out = f'"{val}"' if not re.match(r'^[\w\-#().,% ]+$', val) else val
        else:
            out = None
        add(v['name'], out)
    for s in tokens.get('paintStyles', []):
        ps = visible(s.get('paints'))
        if len(ps) == 1 and ps[0].get('type') == 'SOLID':
            add('color-' + s['name'], rgba(ps[0]['color'], ps[0].get('opacity', 1)))
    lines.append('}')
    for s in tokens.get('textStyles', []):
        fn = s.get('fontName') or {}
        cls = '.text-' + slug(s['name'], 60)
        props = [f'  font-family: "{fn.get("family", "Inter")}", system-ui, sans-serif;',
                 f'  font-weight: {weight_from_style(fn.get("style"))};']
        if s.get('fontSize'):
            props.append(f"  font-size: {px(s['fontSize'])};")
        lh = s.get('lineHeight') or {}
        if lh.get('unit') == 'PIXELS':
            props.append(f"  line-height: {px(lh['value'])};")
        elif lh.get('unit') == 'PERCENT':
            props.append(f"  line-height: {fmt(lh['value'] / 100, 4)};")
        lines.append(cls + ' {\n' + '\n'.join(props) + '\n}')
    return '\n'.join(lines) + '\n'


# --------------------------------------------------------------------------- pipeline


def extract(src: Path, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        if src.resolve() != out.resolve():
            shutil.copytree(src, out, dirs_exist_ok=True)
        return
    root = out.resolve()
    with zipfile.ZipFile(src) as z:
        for info in z.infolist():
            target = (out / info.filename).resolve()
            if not target.is_relative_to(root):
                raise ValueError(f'Unsafe path inside zip: {info.filename}')
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as fsrc, open(target, 'wb') as fdst:
                shutil.copyfileobj(fsrc, fdst)


def relpath(target: Path, start: Path):
    import os
    return Path(os.path.relpath(target, start)).as_posix()


_IMAGES = {}


def _init_worker(images):
    global _IMAGES
    _IMAGES = images


def local_font_css(fonts_dir: Path, out_dir: Path, used=()):
    """Build fonts/fonts.css from font files. Matches the fonts the design uses (family + style) against file names
    (e.g. 'Tobi Pro' + 'ExtraBold' <-> fonnts.com-tobipro-extrabold.otf); other files use 'Family-Style.ext' naming.
    Returns set of family names."""
    fam_set = set()
    rules = []
    fmts = {'.woff2': 'woff2', '.woff': 'woff', '.ttf': 'truetype', '.otf': 'opentype'}
    files = [f for f in sorted(fonts_dir.iterdir()) if f.suffix.lower() in fmts]
    norm = lambda s: re.sub(r'[^a-z0-9]', '', s.lower())
    matched = set()
    for u in used:
        fam, style = u.get('family', ''), u.get('style', 'Regular')
        want = norm(fam) + norm(style)
        cands = [f for f in files if norm(f.stem).endswith(want)]
        if not cands and norm(style) == 'regular':
            cands = [f for f in files if norm(f.stem).endswith(norm(fam)) or norm(f.stem).endswith(norm(fam) + 'regular')]
        if not cands:
            continue
        f = cands[0]
        matched.add(f)
        fam_set.add(fam)
        italic = 'italic' in style.lower()
        rules.append(f'@font-face {{ font-family: "{fam}"; src: url("{f.name}") format("{fmts[f.suffix.lower()]}"); '
                     f'font-weight: {weight_from_style(style)}; font-style: {"italic" if italic else "normal"}; font-display: swap; }}')
    for f in ([] if used else files):  # 'Family-Style.ext' naming only when we do not know the used fonts
        if f in matched:
            continue
        family, _, style = f.stem.partition('-')
        if not style:
            continue
        family = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', family.replace('_', ' ')).strip()
        italic = 'italic' in style.lower()
        fam_set.add(family)
        rules.append(f'@font-face {{ font-family: "{family}"; src: url("{f.name}") format("{fmts[f.suffix.lower()]}"); '
                     f'font-weight: {weight_from_style(style)}; font-style: {"italic" if italic else "normal"}; font-display: swap; }}')
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'fonts.css').write_text('\n'.join(rules) + '\n', 'utf-8')
    return fam_set


def _convert_frame(task):
    """Convert one frame (runs in a worker process). Returns a small dict."""
    import hashlib
    out, fr, mode, fit, elementor, base_url, overrides, local_fonts, cache = task
    out = Path(out)
    local_fonts = set(local_fonts)
    raw = (out / fr['json']).read_bytes()
    page_dir = out / 'html' / slug(fr.get('page') or 'page')
    stem = Path(fr['json']).stem
    fslug = slug(stem, 60)
    html_path = page_dir / f'{fslug}.html'
    epath = out / 'elementor' / f'{slug(fr.get("page"), 30)}--{fslug}.json'
    key = hashlib.sha1(raw + json.dumps([VERSION, mode, fit, elementor, base_url, overrides, sorted(local_fonts)],
                                        sort_keys=True).encode()).hexdigest()
    res = {'id': fr['id'], 'key': key, 'html': relpath(html_path, out), 'warnings': [], 'cached': False}
    if elementor:
        res['elementor'] = relpath(epath, out)
    if cache.get(fr['id']) == key and html_path.exists() and (not elementor or epath.exists()):
        res['cached'] = True
        return res
    node = json.loads(raw.decode('utf-8'))
    page_dir.mkdir(parents=True, exist_ok=True)
    prefix = relpath(out, page_dir) + '/'
    gen = HtmlGen(_IMAGES, prefix, mode=mode)
    gen.overrides = overrides
    body = gen.render_root(node)
    width = num(node.get('width'))
    links = gen.font_links(local_fonts)
    if local_fonts & set(gen.fonts):
        links.append(prefix + 'fonts/fonts.css')
    (page_dir / f'{fslug}.css').write_text(gen.css_text(), 'utf-8')
    html_path.write_text(page_html(fr.get('name', stem), wrap_fit(body, width) if fit else body,
                                   css_href=f'{fslug}.css', font_links=links,
                                   fit_width=width if fit else None), 'utf-8')
    res['warnings'] = gen.warnings
    hdir = out / 'handoff'
    hdir.mkdir(exist_ok=True)
    (hdir / f'{slug(fr.get("page"), 30)}--{fslug}.md').write_text(handoff_md(fr, node, _IMAGES), 'utf-8')
    if elementor:
        epath.parent.mkdir(exist_ok=True)
        tpl = elementor_template(fr.get('name', stem), elementor_widget_html(
            [(None, node)], _IMAGES, base_url, mode, overrides, local_fonts))
        epath.write_text(json.dumps(tpl, indent=2), 'utf-8')
    return res


def convert(src, out, mode='exact', asset_base_url=None, elementor=False, fit=True, log=print,
            workers=None, fonts_dir=None, use_cache=True):
    import time
    from concurrent.futures import ProcessPoolExecutor
    t0 = time.time()
    src, out = Path(src), Path(out)
    if not src.exists():
        raise FileNotFoundError(src)
    log(f'Extracting {src.name} ...')
    extract(src, out)
    manifest_path = out / 'manifest.json'
    if not manifest_path.exists():
        raise ValueError('manifest.json nathi malyu. Aa Figma Export Kit plugin ni zip che?')
    manifest = json.loads(manifest_path.read_text('utf-8'))
    images = manifest.get('images', {})
    export_name = slug(manifest.get('fileName', 'figma-export'), 50)
    if asset_base_url is None:
        asset_base_url = f'/wp-content/uploads/figma-export-kit/{export_name}/'
    if not asset_base_url.endswith('/'):
        asset_base_url += '/'

    tokens_file = out / 'tokens.json'
    if tokens_file.exists():
        try:
            (out / 'tokens.css').write_text(tokens_css(json.loads(tokens_file.read_text('utf-8'))), 'utf-8')
        except Exception as e:  # tokens are optional
            log(f'  ! tokens.css skipped: {e}')

    local_fonts = set()
    if fonts_dir and Path(fonts_dir).is_dir():
        local_fonts = local_font_css(Path(fonts_dir), out / 'fonts', manifest.get('fonts', []))
        for f in Path(fonts_dir).iterdir():
            if f.is_file() and f.suffix.lower() in ('.woff2', '.woff', '.ttf', '.otf'):
                shutil.copy2(f, out / 'fonts' / f.name)
        log(f'  local fonts: {", ".join(sorted(local_fonts)) or "none"}')
    elif (out / 'fonts' / 'fonts.css').exists():
        local_fonts = set(re.findall(r'font-family: "([^"]+)"', (out / 'fonts' / 'fonts.css').read_text('utf-8')))

    ov_all = {}
    if (out / 'overrides.json').exists():
        ov_all = json.loads((out / 'overrides.json').read_text('utf-8'))
    cache_path = out / '.fek-cache.json'
    cache = {}
    if use_cache and cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text('utf-8'))
        except Exception:
            cache = {}

    frames = manifest.get('frames', [])
    tasks = [(str(out), fr, mode, fit, elementor, asset_base_url, ov_all.get(fr['id'], {}), sorted(local_fonts), cache)
             for fr in frames]
    results = []
    n_workers = workers or min(os.cpu_count() or 2, 8)
    if getattr(sys, 'frozen', False):   # inside the EXE: one process (safe; the converter is fast anyway)
        n_workers = 1
    parallel_ok = False
    if len(frames) >= 4 and n_workers > 1:
        try:
            with ProcessPoolExecutor(max_workers=n_workers, initializer=_init_worker, initargs=(images,)) as ex:
                for i, res in enumerate(ex.map(_convert_frame, tasks, chunksize=1), 1):
                    results.append(res)
                    if i % 10 == 0 or i == len(frames):
                        log(f'  {i}/{len(frames)} frames')
            parallel_ok = True
        except Exception as e:  # e.g. process pool unavailable: fall back to one process
            log(f'  parallel mode failed ({type(e).__name__}); single process use karu chu')
            results = []
    if not parallel_ok:
        _init_worker(images)
        for i, t in enumerate(tasks, 1):
            res = _convert_frame(t)
            results.append(res)
            log(f'  [{i}/{len(frames)}] {frames[i - 1].get("page")} / {frames[i - 1].get("name")}'
                + ('  (cached)' if res['cached'] else ''))

    warnings = []
    new_cache = {}
    by_id = {r['id']: r for r in results}
    groups = {}
    for fr in frames:
        r = by_id[fr['id']]
        fr['html'] = r['html']
        if r.get('elementor'):
            fr['elementor'] = r['elementor']
        warnings.extend(r['warnings'])
        new_cache[fr['id']] = r['key']
        base, kind = breakpoint_of(fr.get('name'))
        if base:
            groups.setdefault((fr.get('page'), base), {})[kind] = fr

    manifest['responsive'] = []
    for (page, base), variants in groups.items():
        if len(variants) < 2:
            continue
        page_dir = out / 'html' / slug(page or 'page')
        prefix = relpath(out, page_dir) + '/'
        css_all, body_all, fonts = [], [], {}
        ordered = [k for k in ('desktop', 'tablet', 'mobile') if k in variants]
        nodes = []
        for j, kind in enumerate(ordered):
            fr = variants[kind]
            node = json.loads((out / fr['json']).read_text('utf-8'))
            nodes.append((kind, node))
            gen = HtmlGen(images, prefix, mode=mode)
            gen.overrides = ov_all.get(fr['id'], {})
            body = gen.render_root(node)
            css_all.append(gen.css_text(include_base=(j == 0)))
            for f, v in gen.fonts.items():
                fonts.setdefault(f, set()).update(v)
            body_all.append(f'<div class="fek-bp-{kind}">\n{wrap_fit(body, num(node.get("width")))}</div>\n')
        css_all.append(responsive_rules(ordered))
        lg = HtmlGen({}, '')
        lg.fonts = fonts
        links = lg.font_links(local_fonts)
        if local_fonts & set(fonts):
            links.append(prefix + 'fonts/fonts.css')
        rslug = slug(base, 60) + '.responsive'
        (page_dir / f'{rslug}.css').write_text('\n'.join(css_all) + '\n', 'utf-8')
        (page_dir / f'{rslug}.html').write_text(page_html(
            base, ''.join(body_all), css_href=f'{rslug}.css', font_links=links, fit_width=True), 'utf-8')
        entry = {'name': base, 'page': page, 'breakpoints': ordered, 'html': relpath(page_dir / f'{rslug}.html', out)}
        if elementor:
            ov_by_kind = {k: ov_all.get(variants[k]['id'], {}) for k in ordered}
            tpl = elementor_template(base, elementor_widget_html(
                nodes, images, asset_base_url, mode, ov_by_kind, local_fonts))
            (out / 'elementor').mkdir(exist_ok=True)
            epath = out / 'elementor' / f'{slug(page, 30)}--{rslug}.json'
            epath.write_text(json.dumps(tpl, indent=2), 'utf-8')
            entry['elementor'] = relpath(epath, out)
        manifest['responsive'].append(entry)
        log(f'  [responsive] {base}: {", ".join(ordered)}  ->  {entry["html"]}')

    cache_path.write_text(json.dumps(new_cache), 'utf-8')
    manifest['converter'] = {'version': VERSION, 'mode': mode, 'assetBaseUrl': asset_base_url,
                             'elementor': elementor, 'fit': fit}
    manifest['converterWarnings'] = sorted(set(warnings))
    manifest_path.write_text(json.dumps(manifest, indent=2), 'utf-8')
    (out / 'index.html').write_text(gallery_html(manifest), 'utf-8')
    for w in sorted(set(warnings))[:30]:
        log(f'  ! {w}')
    log(f'Done in {time.time() - t0:.1f}s. Open: {out / "index.html"}')
    return manifest


def gallery_html(m):
    cards = []
    for fr in m.get('frames', []):
        thumb = f'<img src="{esc_attr(fr["render"])}" alt="" loading="lazy">' if fr.get('render') else '<div class="ph"></div>'
        links = [f'<a href="{esc_attr(fr["html"])}">HTML</a>', f'<a href="{esc_attr(fr["json"])}">JSON</a>']
        if fr.get('render'):
            links.append(f'<a href="{esc_attr(fr["render"])}">Figma PNG</a>')
        if fr.get('elementor'):
            links.append(f'<a href="{esc_attr(fr["elementor"])}" download>Elementor</a>')
        if fr.get('verify'):
            links.append(f'<span class="score">{fr["verify"]["match"]}% match</span>')
        cards.append(f'<article><a class="t" href="{esc_attr(fr["html"])}">{thumb}</a><h3>{esc_text(fr.get("name", ""))}</h3>'
                     f'<p>{esc_text(fr.get("page", ""))} · {fmt(fr.get("width", 0))}×{fmt(fr.get("height", 0))}</p>'
                     f'<nav>{" · ".join(links)}</nav></article>')
    resp = ''
    if m.get('responsive'):
        items = ''.join(
            f'<li><a href="{esc_attr(r["html"])}">{esc_text(r["name"])}</a> ({", ".join(r["breakpoints"])})'
            + (f' · <a href="{esc_attr(r["elementor"])}" download>Elementor</a>' if r.get('elementor') else '') + '</li>'
            for r in m['responsive'])
        resp = f'<h2>Responsive pages</h2><ul class="resp">{items}</ul>'
    report = '<p><a href="verify-report.html">Visual verify report</a></p>' if m.get('verified') else ''
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc_text(m.get("fileName", "Figma export"))}</title>
<style>
:root {{ --bg:#f6f6f4; --card:#fff; --text:#1c1c1a; --muted:#6b6b66; --line:#e3e3df; --accent:#2457d6; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#161615; --card:#21211f; --text:#ececea; --muted:#9a9a95; --line:#33332f; --accent:#7ea2ff; }} }}
body {{ margin:0; font:14px/1.5 system-ui, sans-serif; background:var(--bg); color:var(--text); }}
main {{ max-width:1200px; margin:0 auto; padding:24px 16px 48px; }}
h1 {{ font-size:22px; margin:0 0 4px; }} .sub {{ color:var(--muted); margin:0 0 20px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(260px, 1fr)); gap:16px; }}
article {{ background:var(--card); border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
article .t {{ display:block; height:180px; background:var(--line); }}
article img, .ph {{ width:100%; height:180px; object-fit:cover; object-position:top; display:block; }}
article h3 {{ font-size:14px; margin:10px 12px 0; }} article p {{ margin:0 12px; color:var(--muted); font-size:12px; }}
nav {{ padding:8px 12px 12px; font-size:13px; }} a {{ color:var(--accent); }} .score {{ color:var(--muted); }}
.resp {{ padding-left:18px; }}
</style></head><body><main>
<h1>{esc_text(m.get("fileName", "Figma export"))}</h1>
<p class="sub">{len(m.get("frames", []))} frames · {len(m.get("images", {}))} images · mode: {esc_text(m.get("converter", {}).get("mode", ""))}</p>
{report}{resp}
<h2>Frames</h2><div class="grid">{"".join(cards)}</div>
</main></body></html>
'''


def main(argv=None):
    ap = argparse.ArgumentParser(description='Figma Export Kit zip -> JSON + assets + HTML/CSS + Elementor (free, offline)')
    ap.add_argument('input', help='plugin .zip or extracted folder')
    ap.add_argument('-o', '--out', help='output folder (default: <input>-output)')
    ap.add_argument('--mode', choices=['exact', 'responsive'], default='exact',
                    help='exact = pixel-same sizes (default); responsive = hug/fill flex sizing')
    ap.add_argument('--asset-base-url', help='URL where assets/ will live on WordPress '
                    '(default /wp-content/uploads/figma-export-kit/<file-name>/)')
    ap.add_argument('--elementor', action='store_true', help='also write simple Elementor HTML-widget templates (off by default)')
    ap.add_argument('--no-fit', action='store_true', help='do not scale frames down on narrow screens')
    ap.add_argument('--verify', action='store_true', help='pixel-compare HTML vs Figma renders (needs playwright + pillow)')
    ap.add_argument('--autofix', action='store_true', help='verify + replace mismatching nodes by Figma render crops (99%%+)')
    ap.add_argument('--fonts-dir', help='folder with font files named Family-Style.ttf/otf/woff/woff2')
    ap.add_argument('--workers', type=int, help='parallel processes (default: CPU count, max 8)')
    ap.add_argument('--no-cache', action='store_true', help='re-convert every frame')
    a = ap.parse_args(argv)
    src = Path(a.input)
    out = Path(a.out) if a.out else src.with_name(src.stem + '-output')
    if src.suffix.lower() == '.fig':  # read the Figma file offline first
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import fig_import
        fig_import.export(src, out)
        src = out
    convert(src, out, mode=a.mode, asset_base_url=a.asset_base_url, elementor=a.elementor, fit=not a.no_fit,
            workers=a.workers, fonts_dir=a.fonts_dir, use_cache=not a.no_cache)
    if a.verify or a.autofix:
        from verify import verify
        verify(out, autofix=a.autofix)


if __name__ == '__main__':
    sys.exit(main())
