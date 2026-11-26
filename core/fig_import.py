#!/usr/bin/env python3
"""
fig_import.py - read a Figma .fig file OFFLINE (no Figma, no API, no limits) and write the same export folder
that the Figma plugin produces (manifest.json, pages/*.json, assets/), ready for figma2html.py.

.fig = zip { canvas.fig (kiwi: deflate schema + zstd/deflate data), images/<sha1>, meta.json, thumbnail.png }
Requires Python 3.14+ (compression.zstd) for new files; older files use deflate only.

Caveat (be honest): Figma does not document this format; it can change. The plugin path is the reference
implementation. Differences you may see here: masks, component-property overrides, image filters, blend modes.

Usage:
  python fig_import.py "My File.fig" -o out_folder [--pages "Desktop Layout (Final)"] [--frames "Home"] [--list]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import struct
import sys
import time
import zipfile
import zlib
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kiwi  # noqa: E402

SKIP_PAGES = re.compile(r'^(internal only canvas|thumbnail|bin)$', re.I)
TYPE_MAP = {'FRAME': 'FRAME', 'SYMBOL': 'COMPONENT', 'INSTANCE': 'INSTANCE', 'ROUNDED_RECTANGLE': 'RECTANGLE',
            'RECTANGLE': 'RECTANGLE', 'ELLIPSE': 'ELLIPSE', 'TEXT': 'TEXT', 'VECTOR': 'VECTOR', 'LINE': 'LINE',
            'STAR': 'STAR', 'REGULAR_POLYGON': 'POLYGON', 'BOOLEAN_OPERATION': 'BOOLEAN_OPERATION',
            'GROUP': 'GROUP', 'SECTION': 'SECTION'}
VECTOR_LIKE = {'VECTOR', 'LINE', 'STAR', 'REGULAR_POLYGON', 'BOOLEAN_OPERATION'}
IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)  # (a, b, c, d, e, f)  x' = a*x + c*y + e ; y' = b*x + d*y + f


def gid(g):
    return f"{g['sessionID']}:{g['localID']}"


def mat_of(t):
    if not t:
        return IDENT
    return (t['m00'], t['m10'], t['m01'], t['m11'], t['m02'], t['m12'])


def mmul(p, q):  # p * q  (apply q first)
    a1, b1, c1, d1, e1, f1 = p
    a2, b2, c2, d2, e2, f2 = q
    return (a1 * a2 + c1 * b2, b1 * a2 + d1 * b2, a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
            a1 * e2 + c1 * f2 + e1, b1 * e2 + d1 * f2 + f1)


def minv(m):
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        return IDENT
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return (ia, ib, ic, id_, -(ia * e + ic * f), -(ib * e + id_ * f))


def mapply(m, x, y):
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


def r2(v):
    return round(v, 2)


# ------------------------------------------------------------------ loading

def load_fig(path, log=print):
    z = zipfile.ZipFile(path)
    d = z.read('canvas.fig')
    if d[:8] != b'fig-kiwi':
        raise ValueError('canvas.fig header nathi malyu (fig-kiwi). Aa .fig file nathi?')
    off, chunks = 12, []
    while off < len(d):
        n = struct.unpack('<I', d[off:off + 4])[0]
        chunks.append(d[off + 4:off + 4 + n])
        off += 4 + n

    def inflate(raw):
        if raw[:4] == b'\x28\xb5\x2f\xfd':
            try:
                from compression import zstd
            except ImportError as e:
                raise RuntimeError('Aa file zstd vaape che: Python 3.14+ joiye') from e
            return zstd.decompress(raw)
        return zlib.decompress(raw, -15)

    log('Decoding canvas.fig ...')
    t = time.time()
    defs = kiwi.parse_schema(inflate(chunks[0]))
    msg = kiwi.Decoder(defs).decode('Message', inflate(chunks[1]))
    log(f'  {len(msg["nodeChanges"])} nodes in {time.time() - t:.1f}s')
    return z, msg


class Doc:
    def __init__(self, msg):
        self.nodes = msg['nodeChanges']
        self.blobs = [bytes(b['bytes']) for b in msg.get('blobs', [])]
        self.by = {gid(n['guid']): n for n in self.nodes}
        self.kids = defaultdict(list)
        for n in self.nodes:
            p = n.get('parentIndex')
            if p:
                self.kids[gid(p['guid'])].append(n)
        for k in self.kids:
            self.kids[k].sort(key=lambda n: n['parentIndex']['position'])
        self.doc = next(n for n in self.nodes if n['type'] == 'DOCUMENT')

    def pages(self):
        return [p for p in self.kids[gid(self.doc['guid'])] if p['type'] == 'CANVAS']

    def roots(self, page):
        out = []
        for c in self.kids[gid(page['guid'])]:
            if c['type'] == 'SECTION':
                out += [x for x in self.kids[gid(c['guid'])] if x['type'] in ('FRAME', 'SYMBOL')]
            elif c['type'] in ('FRAME', 'SYMBOL'):
                out.append(c)
        return out


# ------------------------------------------------------------------ paints / effects

def color(c):
    return {'r': c['r'], 'g': c['g'], 'b': c['b']}


def conv_paint(p, state):
    if p.get('visible') is False:
        return None
    t = p.get('type')
    op = p.get('opacity', 1.0)
    base = {'visible': True, 'blendMode': p.get('blendMode', 'NORMAL')}
    if t == 'SOLID':
        c = p['color']
        return dict(base, type='SOLID', color=color(c), opacity=op * c.get('a', 1.0))
    if t in ('GRADIENT_LINEAR', 'GRADIENT_RADIAL', 'GRADIENT_ANGULAR', 'GRADIENT_DIAMOND'):
        stops = [{'position': s['position'], 'color': dict(s['color'])} for s in p.get('stops', [])]
        tr = p.get('transform') or {'m00': 1, 'm01': 0, 'm02': 0, 'm10': 0, 'm11': 1, 'm12': 0}
        return dict(base, type=t, opacity=op, gradientStops=stops,
                    gradientTransform=[[tr['m00'], tr['m01'], tr['m02']], [tr['m10'], tr['m11'], tr['m12']]])
    if t == 'IMAGE':
        h = (p.get('image') or {}).get('hash')
        if not h:
            return None
        hx = bytes(h).hex()
        state['images'].add(hx)
        mode = p.get('imageScaleMode', 'FILL')
        out = dict(base, type='IMAGE', imageHash=hx, opacity=op, scaleMode={'STRETCH': 'CROP'}.get(mode, mode))
        if mode == 'STRETCH' and p.get('transform'):
            tr = p['transform']
            out['imageTransform'] = [[tr['m00'], tr['m01'], tr['m02']], [tr['m10'], tr['m11'], tr['m12']]]
        if mode == 'TILE':
            out['scalingFactor'] = p.get('scale', 1.0)
        pf = p.get('paintFilter')
        if pf and any(abs(v) > 0.001 for v in pf.values()):
            state['warn'].add('image filters (exposure/contrast/...) are ignored')
        return out
    state['warn'].add(f'paint type {t} not supported')
    return None


def conv_paints(lst, state):
    return [x for x in (conv_paint(p, state) for p in (lst or [])) if x]


def conv_effects(lst):
    out = []
    for e in lst or []:
        if e.get('visible') is False:
            continue
        t = {'FOREGROUND_BLUR': 'LAYER_BLUR'}.get(e.get('type'), e.get('type'))
        d = {'type': t, 'visible': True, 'radius': e.get('radius', 0), 'blendMode': e.get('blendMode', 'NORMAL')}
        if e.get('color'):
            d['color'] = dict(e['color'])
        if e.get('offset'):
            d['offset'] = dict(e['offset'])
        d['spread'] = e.get('spread', 0)
        out.append(d)
    return out


# ------------------------------------------------------------------ vector geometry -> SVG

def blob_path(b, m, ox, oy, pts):
    i, d, n = 0, [], len(b)

    def pt(x, y):
        X, Y = mapply(m, x, y)
        pts.append((X, Y))
        return f'{r2(X - ox)} {r2(Y - oy)}'
    while i < n:
        c = b[i]
        i += 1
        if c == 0:
            d.append('Z')
        elif c == 1:
            x, y = struct.unpack_from('<ff', b, i); i += 8
            d.append('M' + pt(x, y))
        elif c == 2:
            x, y = struct.unpack_from('<ff', b, i); i += 8
            d.append('L' + pt(x, y))
        elif c == 3:  # quadratic: control + end (verified: 5007/5007 blobs of the real file parse exactly)
            v = struct.unpack_from('<4f', b, i); i += 16
            d.append('Q' + ' '.join(pt(v[k], v[k + 1]) for k in (0, 2)))
        elif c == 4:  # cubic: 2 controls + end
            v = struct.unpack_from('<6f', b, i); i += 24
            d.append('C' + ' '.join(pt(v[k], v[k + 1]) for k in (0, 2, 4)))
        else:
            break
    return ''.join(d)


def hexc(c):
    return '#%02x%02x%02x' % (round(c['r'] * 255), round(c['g'] * 255), round(c['b'] * 255))


def make_svg(doc, node, fills, strokes_paints, fill_geo, stroke_geo, absm, bb, ox_pad=0):
    """Builds an SVG string in absolute coordinates, origin = union box. Returns (svg, box) or None."""
    pts, parts = [], []
    # first pass to get bounds
    geos = [(g, 'fill') for g in fill_geo] + [(g, 'stroke') for g in stroke_geo]
    for g, _ in geos:
        blob_path(doc.blobs[g['commandsBlob']], absm, 0, 0, pts)
    if not pts:
        return None
    x0 = min(min(p[0] for p in pts), bb['x'])
    y0 = min(min(p[1] for p in pts), bb['y'])
    x1 = max(max(p[0] for p in pts), bb['x'] + bb['width'])
    y1 = max(max(p[1] for p in pts), bb['y'] + bb['height'])
    x0, y0 = math.floor(x0 * 100) / 100, math.floor(y0 * 100) / 100
    W, H = max(0.01, x1 - x0), max(0.01, y1 - y0)
    defs, body = [], []
    size = node.get('size') or {'x': 1, 'y': 1}
    sm = (1, 0, 0, 1, -x0, -y0)

    def paint_attr(p, idx):
        if p['type'] == 'SOLID':
            return f'fill="{hexc(p["color"])}"' + (f' fill-opacity="{r2(p["opacity"])}"' if p['opacity'] < 0.999 else '')
        if p['type'] in ('GRADIENT_LINEAR', 'GRADIENT_RADIAL'):
            gt = p['gradientTransform']
            T = (gt[0][0], gt[1][0], gt[0][1], gt[1][1], gt[0][2], gt[1][2])
            mg = mmul(sm, mmul(absm, mmul((size['x'], 0, 0, size['y'], 0, 0), minv(T))))
            gm = 'matrix(' + ' '.join(str(round(v, 5)) for v in mg) + ')'
            stops = ''.join(f'<stop offset="{r2(s["position"])}" stop-color="{hexc(s["color"])}" stop-opacity="{r2(s["color"].get("a", 1) * p["opacity"])}"/>'
                            for s in p['gradientStops'])
            gid_ = f'g{idx}'
            if p['type'] == 'GRADIENT_LINEAR':
                defs.append(f'<linearGradient id="{gid_}" gradientUnits="userSpaceOnUse" x1="0" y1="0.5" x2="1" y2="0.5" gradientTransform="{gm}">{stops}</linearGradient>')
            else:
                defs.append(f'<radialGradient id="{gid_}" gradientUnits="userSpaceOnUse" cx="0.5" cy="0.5" r="0.5" gradientTransform="{gm}">{stops}</radialGradient>')
            return f'fill="url(#{gid_})"'
        return None

    n_id = 0
    for plist, geo, kind in ((fills, fill_geo, 'fill'), (strokes_paints, stroke_geo, 'stroke')):
        paints = [p for p in plist if p['type'] != 'IMAGE']
        for p in paints:
            attr = paint_attr(p, n_id)
            n_id += 1
            if not attr:
                continue
            for g in geo:
                dd = blob_path(doc.blobs[g['commandsBlob']], absm, x0, y0, [])
                rule = ' fill-rule="evenodd"' if g.get('windingRule') == 'ODD' else ''
                body.append(f'<path d="{dd}" {attr}{rule}/>')
    if not body:
        return None
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{r2(W)}" height="{r2(H)}" viewBox="0 0 {r2(W)} {r2(H)}">'
           + (f'<defs>{"".join(defs)}</defs>' if defs else '') + ''.join(body) + '</svg>')
    return svg, {'x': x0, 'y': y0, 'width': r2(W), 'height': r2(H)}


# ------------------------------------------------------------------ node conversion

class Exporter:
    def __init__(self, doc, out: Path, log=print):
        self.doc, self.out, self.log = doc, out, log
        self.state = {'images': set(), 'warn': set()}
        self.fonts = {}
        self.svg_seen = {}
        self.stats = defaultdict(int)
        (out / 'assets' / 'svg').mkdir(parents=True, exist_ok=True)

    # ---- sizing helpers
    @staticmethod
    def own_sizing(n):
        """(horizontal, vertical) sizing of the node itself: FIXED | HUG"""
        if n['type'] == 'TEXT':
            ar = n.get('textAutoResize')
            return ('HUG' if ar == 'WIDTH_AND_HEIGHT' else 'FIXED', 'HUG' if ar in ('WIDTH_AND_HEIGHT', 'HEIGHT') else 'FIXED')
        sm = n.get('stackMode')
        if sm not in ('HORIZONTAL', 'VERTICAL'):
            return 'FIXED', 'FIXED'
        prim = 'HUG' if str(n.get('stackPrimarySizing', '')).startswith('RESIZE_TO_FIT') else 'FIXED'
        cnt = 'HUG' if str(n.get('stackCounterSizing', '')).startswith('RESIZE_TO_FIT') else 'FIXED'
        return (prim, cnt) if sm == 'HORIZONTAL' else (cnt, prim)

    def common(self, n, o, parent_n):
        st = self.state
        o['opacity'] = n.get('opacity', 1.0)
        bm = n.get('blendMode')
        if bm and bm != 'PASS_THROUGH':
            o['blendMode'] = bm
        if n.get('mask'):
            o['isMask'] = True
        o['fills'] = conv_paints(n.get('fillPaints'), st)
        o['strokes'] = conv_paints(n.get('strokePaints'), st)
        if o['strokes']:
            o['strokeWeight'] = n.get('strokeWeight', 1)
            o['strokeAlign'] = n.get('strokeAlign', 'INSIDE')
            if n.get('dashPattern'):
                o['dashPattern'] = list(n['dashPattern'])
            if n.get('borderStrokeWeightsIndependent'):
                for k, s in (('Top', 'strokeTopWeight'), ('Right', 'strokeRightWeight'),
                             ('Bottom', 'strokeBottomWeight'), ('Left', 'strokeLeftWeight')):
                    o[s] = n.get(f'border{k}Weight', 0)
        o['effects'] = conv_effects(n.get('effects'))
        # corners
        ind = [n.get(k) for k in ('rectangleTopLeftCornerRadius', 'rectangleTopRightCornerRadius',
                                  'rectangleBottomRightCornerRadius', 'rectangleBottomLeftCornerRadius')]
        if any(v is not None for v in ind):
            base = n.get('cornerRadius', 0) or 0
            vals = [base if v is None else v for v in ind]
            if len(set(vals)) == 1:
                o['cornerRadius'] = vals[0]
            else:
                o['cornerRadius'] = 'MIXED'
                o['topLeftRadius'], o['topRightRadius'], o['bottomRightRadius'], o['bottomLeftRadius'] = vals
        elif n.get('cornerRadius'):
            o['cornerRadius'] = n['cornerRadius']
        if n['type'] in ('FRAME', 'SYMBOL', 'INSTANCE'):
            o['clipsContent'] = not n.get('frameMaskDisabled', True)
        # auto layout
        sm = n.get('stackMode')
        if sm in ('HORIZONTAL', 'VERTICAL'):
            o['layoutMode'] = sm
            if n.get('stackWrap') == 'WRAP':
                o['layoutWrap'] = 'WRAP'
                o['counterAxisSpacing'] = n.get('stackCounterSpacing', 0)
            pa = n.get('stackPrimaryAlignItems', 'MIN')
            o['primaryAxisAlignItems'] = 'SPACE_BETWEEN' if pa == 'SPACE_EVENLY' else pa
            o['counterAxisAlignItems'] = n.get('stackCounterAlignItems', 'MIN')
            o['itemSpacing'] = n.get('stackSpacing', 0)
            hp, vp = n.get('stackHorizontalPadding'), n.get('stackVerticalPadding')
            o['paddingLeft'] = n.get('stackPaddingLeft', hp or 0)
            o['paddingRight'] = n.get('stackPaddingRight', hp or 0)
            o['paddingTop'] = n.get('stackPaddingTop', vp or 0)
            o['paddingBottom'] = n.get('stackPaddingBottom', vp or 0)
        if n.get('stackPositioning') == 'ABSOLUTE':
            o['layoutPositioning'] = 'ABSOLUTE'
        if n.get('stackChildPrimaryGrow'):
            o['layoutGrow'] = n['stackChildPrimaryGrow']
        if n.get('stackChildAlignSelf') == 'STRETCH':
            o['layoutAlign'] = 'STRETCH'
        h, v = self.own_sizing(n)
        if parent_n is not None and parent_n.get('stackMode') in ('HORIZONTAL', 'VERTICAL') and n.get('stackPositioning') != 'ABSOLUTE':
            horiz = parent_n['stackMode'] == 'HORIZONTAL'
            if n.get('stackChildPrimaryGrow'):
                if horiz:
                    h = 'FILL'
                else:
                    v = 'FILL'
            if n.get('stackChildAlignSelf') == 'STRETCH':
                if horiz:
                    v = 'FILL'
                else:
                    h = 'FILL'
        o['layoutSizingHorizontal'], o['layoutSizingVertical'] = h, v
        if n.get('maxSize'):
            o['maxWidth'] = n['maxSize'].get('x')
            o['maxHeight'] = n['maxSize'].get('y')

    def text(self, n, o):
        td = n.get('textData') or {}
        chars = td.get('characters', '')
        base = {k: n.get(k) for k in ('fontName', 'fontSize', 'lineHeight', 'letterSpacing', 'textDecoration', 'textCase')}
        base['fillPaints'] = n.get('fillPaints')
        ids = td.get('characterStyleIDs')
        table = {s['styleID']: s for s in td.get('styleOverrideTable', [])}
        runs = []
        if ids and len(ids) >= 1:
            start = 0
            for i in range(1, len(chars) + 1):
                if i == len(chars) or (i < len(ids) and ids[i] != ids[start]) or (i >= len(ids) and (ids[start] if start < len(ids) else 0) != 0):
                    runs.append((start, i, ids[start] if start < len(ids) else 0))
                    start = i
        else:
            runs = [(0, len(chars), 0)]
        segs = []
        for a, b, sid in runs:
            s = dict(base)
            if sid and sid in table:
                for k, v in table[sid].items():
                    if k != 'styleID':
                        s[k] = v
            fn = s.get('fontName') or {'family': 'Inter', 'style': 'Regular'}
            lh = s.get('lineHeight') or {'value': 100, 'units': 'RAW'}
            if lh.get('units') == 'PIXELS':
                lhv = {'unit': 'PIXELS', 'value': lh['value']}
            elif lh.get('units') == 'PERCENT':
                lhv = {'unit': 'PERCENT', 'value': lh['value']}
            else:
                lhv = {'unit': 'PERCENT', 'value': lh.get('value', 1) * 100}
            ls = s.get('letterSpacing') or {'value': 0, 'units': 'PIXELS'}
            lsv = {'unit': 'PIXELS' if ls.get('units') == 'PIXELS' else 'PERCENT', 'value': ls.get('value', 0)}
            seg = {'characters': chars[a:b], 'start': a, 'end': b,
                   'fontName': {'family': fn['family'], 'style': fn['style']}, 'fontSize': s.get('fontSize', 16),
                   'lineHeight': lhv, 'letterSpacing': lsv,
                   'fills': conv_paints(s.get('fillPaints'), self.state),
                   'textDecoration': s.get('textDecoration', 'NONE'), 'textCase': s.get('textCase', 'ORIGINAL')}
            link = s.get('hyperlink')
            if isinstance(link, dict) and link.get('url'):
                seg['hyperlink'] = {'type': 'URL', 'value': link['url']}
            self.fonts[(fn['family'], fn['style'])] = {'family': fn['family'], 'style': fn['style']}
            segs.append(seg)
        o['characters'] = chars
        o['segments'] = segs
        o['textAlignHorizontal'] = n.get('textAlignHorizontal', 'LEFT')
        o['textAlignVertical'] = n.get('textAlignVertical', 'TOP')
        o['textAutoResize'] = n.get('textAutoResize', 'NONE')
        if n.get('textTruncation') == 'ENDING':
            o['textTruncation'] = 'ENDING'
            o['maxLines'] = n.get('maxLines')

    def asset_svg(self, n, o, absm, bb):
        fg = [g for g in (n.get('fillGeometry') or []) if g.get('commandsBlob', -1) < len(self.doc.blobs)]
        sg = [g for g in (n.get('strokeGeometry') or []) if g.get('commandsBlob', -1) < len(self.doc.blobs)]
        fills = o.get('fills', [])
        strokes = o.get('strokes', [])
        if any(p['type'] == 'IMAGE' for p in fills):
            self.state['warn'].add('image fill on a vector shape is ignored')
        res = make_svg(self.doc, n, fills, strokes, fg if fills else [], sg if strokes else [], absm, bb)
        if not res:
            return False
        svg, box = res
        h = hashlib.sha1(svg.encode()).hexdigest()[:10]
        path = self.svg_seen.get(h)
        if not path:
            path = f'assets/svg/{re.sub(r"[^a-z0-9]+", "-", n.get("name", "v").lower()).strip("-")[:30] or "v"}-{h}.svg'
            (self.out / path).write_text(svg, 'utf-8')
            self.svg_seen[h] = path
        o['asset'] = {'path': path, 'format': 'svg', 'scale': 1, 'renderBounds': box, 'cssVisuals': True}
        self.stats['svg'] += 1
        return True

    def conv(self, n, parent_abs, parent_n, ov, dv, idp, symbol_children=None):
        """n: raw node; ov/dv: override/derived maps (key tuple of guid strs -> dict) relative to current instance."""
        g = gid(n['guid'])
        key = (g,)
        if key in ov:
            n = {**n, **{k: v for k, v in ov[key].items() if k != 'guidPath'}}
        if key in dv:
            d = dv[key]
            n = {**n, **{k: v for k, v in d.items() if k in ('size', 'transform', 'fillGeometry', 'strokeGeometry') and v is not None}}
        if n.get('visible') is False:
            return None
        t = n['type']
        if t not in TYPE_MAP:
            return None
        size = n.get('size') or {'x': 0, 'y': 0}
        rel = mat_of(n.get('transform'))
        absm = mmul(parent_abs, rel)
        pts = [mapply(absm, 0, 0), mapply(absm, size['x'], 0), mapply(absm, 0, size['y']), mapply(absm, size['x'], size['y'])]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        bb = {'x': r2(min(xs)), 'y': r2(min(ys)), 'width': r2(max(xs) - min(xs)), 'height': r2(max(ys) - min(ys))}
        o = {'id': idp + g, 'name': n.get('name', ''), 'type': TYPE_MAP[t], 'width': size['x'], 'height': size['y'],
             'absoluteTransform': [[absm[0], absm[2], absm[4]], [absm[1], absm[3], absm[5]]], 'absoluteBoundingBox': bb}
        self.stats['nodes'] += 1
        self.common(n, o, parent_n)
        if n.get('mask'):
            self.mask_svg(n, o, absm, bb)

        arc = n.get('arcData')
        partial_arc = t == 'ELLIPSE' and arc and (arc.get('innerRadius', 0) > 0 or abs(arc.get('endingAngle', 0) - arc.get('startingAngle', 0)) < 6.28)
        if t in VECTOR_LIKE or partial_arc:
            if self.asset_svg(n, o, absm, bb):
                return o
        if t == 'TEXT':
            self.text(n, o)
            return o

        children = []
        if t == 'INSTANCE':
            sym = self.doc.by.get(gid(n['symbolData']['symbolID'])) if n.get('symbolData') else None
            if sym is not None:
                own_ov = {tuple(gid(x) for x in e['guidPath']['guids']): e for e in n['symbolData'].get('symbolOverrides', [])}
                own_dv = {tuple(gid(x) for x in e['guidPath']['guids']): e for e in n.get('derivedSymbolData', [])}
                ov2 = {**own_ov, **ov}
                dv2 = {**own_dv, **dv}
                sym_key = (gid(sym['guid']),)
                if sym_key in ov2:
                    for k in ('fillPaints', 'strokePaints', 'effects', 'opacity'):
                        if k in ov2[sym_key]:
                            n = {**n, k: ov2[sym_key][k]}
                            if k in ('fillPaints', 'strokePaints'):
                                o['fills' if k == 'fillPaints' else 'strokes'] = conv_paints(n[k], self.state)
                            elif k == 'effects':
                                o['effects'] = conv_effects(n[k])
                            else:
                                o['opacity'] = n[k]
                for c in self.doc.kids[gid(sym['guid'])]:
                    cid = c['guid']
                    sub_ov, sub_dv = self.descend(ov2, dv2, gid(cid))
                    r = self.conv(c, absm, n if n.get('stackMode') else None, sub_ov, sub_dv, o['id'] + ';', None)
                    if r:
                        children.append(r)
                o['children'] = self.apply_masks(children)
                return o
        for c in self.doc.kids[g]:
            r = self.conv(c, absm, n, ov, dv, idp, None)
            if r:
                children.append(r)
        if t in ('FRAME', 'SYMBOL', 'INSTANCE', 'GROUP', 'SECTION') or children:
            o['children'] = self.apply_masks(children)
        return o

    def mask_svg(self, n, o, absm, bb):
        """A mask node becomes an SVG alpha-mask (black shape) that apply_masks() puts on a wrapper of the masked siblings."""
        fg = [g for g in (n.get('fillGeometry') or []) if g.get('commandsBlob', -1) < len(self.doc.blobs)]
        black = {'type': 'SOLID', 'color': {'r': 0, 'g': 0, 'b': 0}, 'opacity': 1.0}
        res = make_svg(self.doc, n, [black], [], fg, [], absm, bb) if fg else None
        if not res:
            self.state['warn'].add('some masks have no geometry and are ignored')
            return
        svg, box = res
        h = hashlib.sha1(svg.encode()).hexdigest()[:10]
        path = f'assets/svg/mask-{h}.svg'
        (self.out / path).write_text(svg, 'utf-8')
        o['maskSvg'] = {'path': path, 'box': box, 'svg': svg}
        self.stats['masks'] += 1

    @staticmethod
    def apply_masks(children):
        """Figma: a mask layer clips every sibling above it (later in the list) until the next mask."""
        if not any(c.get('isMask') for c in children):
            return children
        out, cur = [], None
        for c in children:
            if c.get('isMask'):
                ms = c.get('maskSvg')
                cur = None
                if ms:
                    b = ms['box']
                    cur = {'id': 'mask:' + c['id'], 'name': 'Mask group', 'type': 'GROUP', 'width': b['width'], 'height': b['height'],
                           'absoluteTransform': [[1, 0, b['x']], [0, 1, b['y']]], 'absoluteBoundingBox': b,
                           'layoutPositioning': 'ABSOLUTE', 'maskAsset': ms['path'], 'maskData': ms['svg'], 'opacity': c.get('opacity', 1.0), 'children': []}
                    out.append(cur)
                continue
            (cur['children'] if cur is not None else out).append(c)
        return out

    @staticmethod
    def descend(ov, dv, child_guid):
        """Maps for a nested instance: keep entries whose path starts with the child, with that prefix stripped,
        and keep single-guid entries as they are (they address nodes by their own guid)."""
        sub_ov = dict(ov)
        sub_dv = dict(dv)
        for k, v in ov.items():
            if len(k) > 1 and k[0] == child_guid:
                sub_ov[k[1:]] = v
        for k, v in dv.items():
            if len(k) > 1 and k[0] == child_guid:
                sub_dv[k[1:]] = v
        return sub_ov, sub_dv


# ------------------------------------------------------------------ images

def sniff_ext(b):
    if b[:4] == b'\x89PNG':
        return '.png'
    if b[:2] == b'\xff\xd8':
        return '.jpg'
    if b[:4] == b'GIF8':
        return '.gif'
    if b[:4] == b'RIFF' and b[8:12] == b'WEBP':
        return '.webp'
    return '.png'


def extract_images(z, hashes, out: Path, log):
    images = {}
    (out / 'assets' / 'images').mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image
    except ImportError:
        Image = None
    names = set(z.namelist())
    for i, h in enumerate(sorted(hashes), 1):
        name = f'images/{h}'
        if name not in names:
            continue
        with z.open(name) as src:
            head = src.read(16)
            ext = sniff_ext(head)
            dest = out / 'assets' / 'images' / (h + ext)
            with open(dest, 'wb') as dst:
                dst.write(head)
                shutil.copyfileobj(src, dst, 1 << 20)
        w = hh = 0
        if Image:
            try:
                with Image.open(dest) as im:
                    w, hh = im.size
            except Exception:
                pass
        images[h] = {'path': f'assets/images/{h}{ext}', 'width': w, 'height': hh}
        if i % 25 == 0:
            log(f'  images {i}/{len(hashes)}')
    return images


# ------------------------------------------------------------------ main

def safe(s):
    s = re.sub(r'[\\/:*?"<>|#%{}^~\[\]`]+', '_', str(s or 'untitled')).strip()
    return (re.sub(r'\s+', ' ', s)[:60].strip() or 'untitled')


def export(fig_path, out, pages=None, frames=None, max_frames=None, log=print):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    z, msg = load_fig(fig_path, log)
    doc = Doc(msg)
    ex = Exporter(doc, out, log)
    meta = {}
    try:
        meta = json.loads(z.read('meta.json'))
    except Exception:
        pass
    sel_pages = [p for p in doc.pages() if (pages and p['name'] in pages) or (not pages and not SKIP_PAGES.match(p['name']))]
    manifest_frames, used = [], set()
    count = 0
    for pg in sel_pages:
        for root in doc.roots(pg):
            if frames and not any(f.lower() in root['name'].lower() for f in frames):
                continue
            if max_frames and count >= max_frames:
                break
            t0 = time.time()
            o = ex.conv(root, IDENT, None, {}, {}, '')
            if not o:
                continue
            # root frame position on the canvas is irrelevant: make the root the origin
            page_dir = out / 'pages' / safe(pg['name'])
            page_dir.mkdir(parents=True, exist_ok=True)
            base, i = safe(root['name']), 2
            key = lambda b: (pg['name'], re.sub(r'[^a-z0-9]+', '-', b.lower()).strip('-'))  # same form figma2html uses for file names
            while key(base) in used:
                base = f'{safe(root["name"])}-{i}'
                i += 1
            used.add(key(base))
            jp = page_dir / f'{base}.json'
            jp.write_text(json.dumps(o), 'utf-8')
            manifest_frames.append({'id': o['id'], 'name': root['name'], 'page': pg['name'], 'type': o['type'],
                                    'width': o['width'], 'height': o['height'], 'json': jp.relative_to(out).as_posix()})
            count += 1
            log(f'  {pg["name"]} / {root["name"]}  {o["width"]:.0f}x{o["height"]:.0f}  ({time.time() - t0:.1f}s)')
    log(f'Extracting {len(ex.state["images"])} images ...')
    images = extract_images(z, ex.state['images'], out, log)
    manifest = {'tool': 'figma-export-kit', 'version': 1, 'source': 'fig-file', 'fileName': meta.get('file_name') or Path(fig_path).stem,
                'exportedAt': meta.get('exported_at'), 'frames': manifest_frames, 'images': images,
                'fonts': sorted(ex.fonts.values(), key=lambda f: (f['family'], f['style'])),
                'warnings': sorted(ex.state['warn']), 'stats': dict(ex.stats, frames=len(manifest_frames), images=len(images))}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2), 'utf-8')
    (out / 'tokens.json').write_text(json.dumps({'paintStyles': [], 'textStyles': [], 'effectStyles': [],
                                                 'variableCollections': [], 'variables': []}), 'utf-8')
    log(f'Exported {len(manifest_frames)} frames, {ex.stats["nodes"]} nodes, {ex.stats["svg"]} svg, {len(images)} images')
    for w in manifest['warnings']:
        log(f'  ! {w}')
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description='Read a .fig file offline and write a Figma Export Kit export folder')
    ap.add_argument('fig')
    ap.add_argument('-o', '--out', help='output folder')
    ap.add_argument('--pages', nargs='*', help='page names to export (default: all except Bin/Internal/Thumbnail)')
    ap.add_argument('--frames', nargs='*', help='only frames whose name contains one of these')
    ap.add_argument('--max-frames', type=int)
    ap.add_argument('--list', action='store_true', help='list pages and top-level frames, then exit')
    a = ap.parse_args(argv)
    if a.list:
        z, msg = load_fig(a.fig)
        doc = Doc(msg)
        for pg in doc.pages():
            rs = doc.roots(pg)
            print(f'PAGE {pg["name"]!r}: {len(rs)} frames')
            for r in rs[:12]:
                s = r.get('size', {})
                print(f'   {r["name"]!r} {s.get("x", 0):.0f}x{s.get("y", 0):.0f}')
        return
    if not a.out:
        ap.error('-o/--out required')
    export(a.fig, a.out, a.pages, a.frames, a.max_frames)


if __name__ == '__main__':
    main()
