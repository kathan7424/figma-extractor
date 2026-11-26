"""
elementor_native.py - Figma export JSON  ->  NATIVE Elementor (containers + widgets), following the DeveloperAI skill:

  * native containers/widgets only; NO custom CSS, NO class-based CSS (skill 114)
  * global design system first: Global Colors / Global Typography / container width in the Kit (skill 98-102, 118)
  * boxed inner container on the global width; full-bleed sections = Full Width outer + Boxed inner (skill 118)
  * images -> WebP in the Media Library (skill 131); alt text from layer names (skill 136)
  * every clickable element linked, via a link map (skill 145)

Pure stdlib (+ Pillow only in media.py). Input = the export folder produced by fig_import.py / the Figma plugin.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import dynamic

MIXED = 'MIXED'


# ----------------------------------------------------------------------------- small helpers

def num(v, d=0.0):
    return d if v is None or v == MIXED else float(v)


def rnd(v, p=0):
    return int(round(v)) if p == 0 else round(v, p)


def eid():
    return hashlib.sha1(str(id(object())).encode() + str(len(_IDS)).encode()).hexdigest()[:7]


_IDS = []


def new_id():
    _IDS.append(1)
    return hashlib.md5(str(len(_IDS)).encode()).hexdigest()[:7]


def slug(s, n=40):
    s = re.sub(r'[^a-zA-Z0-9]+', '-', str(s or '')).strip('-').lower()[:n].strip('-')
    return s or 'item'


def size(v, unit='px'):
    return {'unit': unit, 'size': v, 'sizes': []}


def dims(t, r=None, b=None, l=None, unit='px'):
    r = t if r is None else r
    b = t if b is None else b
    l = r if l is None else l
    return {'unit': unit, 'top': str(rnd(t)), 'right': str(rnd(r)), 'bottom': str(rnd(b)), 'left': str(rnd(l)),
            'isLinked': t == r == b == l}


def hexa(c, opacity=1.0):
    r, g, b = (round(c.get(k, 0) * 255) for k in ('r', 'g', 'b'))
    a = c.get('a', 1.0) * opacity
    if a >= 0.995:
        return '#%02x%02x%02x' % (r, g, b)
    return f'rgba({r},{g},{b},{round(a, 3)})'


def with_alpha(hexcol, a):
    """#rrggbb (or rgba()) + extra alpha -> rgba(); Elementor has no plain opacity control, text/background alpha is the native way."""
    if a >= 0.995:
        return hexcol
    if hexcol.startswith('#') and len(hexcol) == 7:
        r, g, b = (int(hexcol[i:i + 2], 16) for i in (1, 3, 5))
        return f'rgba({r},{g},{b},{round(a, 3)})'
    m = re.match(r'rgba\((\d+),(\d+),(\d+),([\d.]+)\)', hexcol)
    if m:
        return f'rgba({m.group(1)},{m.group(2)},{m.group(3)},{round(float(m.group(4)) * a, 3)})'
    return hexcol


def visible_paints(paints):
    return [p for p in (paints or []) if isinstance(p, dict) and p.get('visible', True) is not False and p.get('opacity', 1) > 0.01]


def bbox(n):
    b = n.get('absoluteBoundingBox')
    if b:
        return b['x'], b['y'], b['width'], b['height']
    t = n.get('absoluteTransform') or [[1, 0, 0], [0, 1, 0]]
    return t[0][2], t[1][2], num(n.get('width')), num(n.get('height'))


def kids(n):
    return [c for c in n.get('children') or [] if c.get('visible') is not False and not c.get('isMask')]


# ----------------------------------------------------------------------------- design system (colors, typography)

NEUTRAL_NAMES = {'#ffffff': 'White', '#000000': 'Black'}


def lum(hexcol):
    h = hexcol.lstrip('#')
    if len(h) != 6:
        return 0.5
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def sat(hexcol):
    h = hexcol.lstrip('#')
    if len(h) != 6:
        return 0
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return max(r, g, b) - min(r, g, b)


class DesignSystem:
    """Collects every color and text style in the design, then assigns Elementor globals."""

    def __init__(self):
        self.color_use = Counter()
        self.text_use = Counter()
        self.text_keys = {}
        self.colors = {}   # hex -> global id
        self.typos = {}    # key -> global id
        self.kit = {}

    def add_color(self, hexcol, weight=1):
        if hexcol and hexcol.startswith('#'):
            self.color_use[hexcol.lower()] += weight

    def add_text(self, key, weight=1):
        self.text_use[key] += weight

    def finish(self, container_width=None):
        pal = [c for c, _ in self.color_use.most_common(24)]
        chromatic = [c for c in pal if sat(c) > 0.18 and 0.12 < lum(c) < 0.92]
        darks = [c for c in pal if lum(c) < 0.25 and sat(c) <= 0.4]
        names = {}
        if chromatic:
            names[chromatic[0]] = ('primary', 'Primary')
        if len(chromatic) > 1:
            names[chromatic[1]] = ('secondary', 'Secondary')
        if darks:
            names[darks[0]] = ('text', 'Text')
        for c in pal:
            if c in NEUTRAL_NAMES and c not in names:
                names[c] = (NEUTRAL_NAMES[c].lower(), NEUTRAL_NAMES[c])
        accent = [c for c in chromatic if c not in names]
        if accent:
            names[accent[0]] = ('accent', 'Accent')
        sys_ids = {'primary', 'secondary', 'text', 'accent'}
        system, custom, n = [], [], 1
        for c in pal:
            if self.color_use[c] < 2 and c not in names:
                continue
            gid, title = names.get(c, (None, None))
            if gid is None:
                gid, title = f'c{hashlib.md5(c.encode()).hexdigest()[:6]}', f'Color {n}'
                n += 1
            self.colors[c] = gid
            (system if gid in sys_ids else custom).append({'_id': gid, 'title': title, 'color': c.upper()})
        # typography: styles used >= 3 times become globals; the 4 most used are the "system" ones
        ranked = [k for k, v in self.text_use.most_common(14) if v >= 3]
        sys_t, cus_t = [], []
        for i, k in enumerate(ranked):
            fam, sz, wt, lh, ls, tt = self.text_keys[k]
            gid = ['primary', 'secondary', 'text', 'accent'][i] if i < 4 else f't{hashlib.md5(k.encode()).hexdigest()[:6]}'
            title = ['Primary', 'Secondary', 'Text', 'Accent'][i] if i < 4 else f'Style {i + 1}'
            entry = {'_id': gid, 'title': title, 'typography_typography': 'custom', 'typography_font_family': fam,
                     'typography_font_size': size(sz), 'typography_font_weight': str(wt)}
            if lh:
                entry['typography_line_height'] = lh
            if ls:
                entry['typography_letter_spacing'] = ls
            if tt:
                entry['typography_text_transform'] = tt
            (sys_t if i < 4 else cus_t).append(entry)
            self.typos[k] = gid
        self.kit = {'system_colors': [c for c in system if c['_id'] in sys_ids], 'custom_colors': custom + [c for c in system if c['_id'] not in sys_ids],
                    'system_typography': sys_t, 'custom_typography': cus_t}
        if container_width:
            self.kit['container_width'] = size(container_width)
            self.kit['container_padding'] = dims(0)
            self.kit['space_between_widgets'] = {'unit': 'px', 'column': '0', 'row': '0', 'isLinked': True, 'size': 0}
        return self.kit

    def global_color(self, hexcol):
        return self.colors.get((hexcol or '').lower())


# ----------------------------------------------------------------------------- items + layout inference

@dataclass
class Item:
    x: float
    y: float
    w: float
    h: float
    el: dict
    kind: str = 'widget'           # widget | container | decor
    node: dict = field(default_factory=dict)
    margin: tuple = (0, 0, 0, 0)
    z: int = 0                     # paint order in the Figma frame (1 = back)

    @property
    def r(self):
        return self.x + self.w

    @property
    def b(self):
        return self.y + self.h

    @property
    def cx(self):
        return self.x + self.w / 2

    @property
    def cy(self):
        return self.y + self.h / 2


def v_overlap(a, b):
    return min(a.b, b.b) - max(a.y, b.y)


def h_overlap(a, b):
    return min(a.r, b.r) - max(a.x, b.x)


def union(items):
    x0 = min(i.x for i in items)
    y0 = min(i.y for i in items)
    return x0, y0, max(i.r for i in items) - x0, max(i.b for i in items) - y0


def mode_gap(gaps):
    if not gaps:
        return 0
    c = Counter(round(g) for g in gaps)
    return c.most_common(1)[0][0]


def cluster_rows(items):
    """Group items whose vertical ranges overlap (same visual row)."""
    rows = []
    for it in sorted(items, key=lambda i: (i.y, i.x)):
        placed = False
        for row in rows:
            ry0 = min(i.y for i in row)
            ry1 = max(i.b for i in row)
            ov = min(it.b, ry1) - max(it.y, ry0)
            small = min(it.h, ry1 - ry0) or 1
            if ov > 0.5 * small:
                row.append(it)
                placed = True
                break
        if not placed:
            rows.append([it])
    for row in rows:
        row.sort(key=lambda i: i.x)
    rows.sort(key=lambda r: min(i.y for i in r))
    return rows


def split_columns(items):
    """If the items form side-by-side vertical stacks (e.g. a two-column section), split by horizontal gaps."""
    its = sorted(items, key=lambda i: i.x)
    groups, cur, right = [], [its[0]], its[0].r
    for it in its[1:]:
        if it.x >= right - 1:  # no horizontal overlap with the group so far -> new column
            groups.append(cur)
            cur, right = [it], it.r
        else:
            cur.append(it)
            right = max(right, it.r)
    groups.append(cur)
    return groups


# ----------------------------------------------------------------------------- the builder

class Builder:
    def __init__(self, export_dir, manifest, ds: DesignSystem, link_map=None, pro=False, log=print):
        self.dir = Path(export_dir)
        self.m = manifest
        self.images = manifest.get('images', {})
        self.ds = ds
        self.link_map = link_map or {}
        self.pro = pro
        self.log = log
        self.media = {}      # key -> {'path': relative file, 'alt': str, 'kind': 'image'|'svg'}
        self.warn = Counter()
        self.links_todo = []
        self.stats = Counter()
        self.fonts_used = set()
        self.raster = {}          # node id -> PNG path (exact-HTML render of masked groups)
        self.exact = False        # True = pixel-exact mode: every element placed with Elementor's native absolute positioning
        self.el_by_node = {}      # id(figma node) -> Elementor widget id (for dynamic tags)
        self.loops = {}           # id(card node) -> loop info (Elementor Pro Loop Grid)
        self.loop_templates = {}  # post type -> loop item element
        self.section_nodes = []
        self.container_w = 1140

    # ---------- colors / typography
    def color_setting(self, settings, key, hexcol, gkey=None):
        """Set a color; link it to a Global Color when the design system has it."""
        settings[key] = hexcol
        g = self.ds.global_color(hexcol)
        if g:
            settings.setdefault('__globals__', {})[key] = f'globals/colors?id={g}'

    def seg_style(self, n):
        segs = n.get('segments') or []
        s = max(segs, key=lambda x: len(x.get('characters', ''))) if segs else {}
        fn = s.get('fontName') or {'family': 'Inter', 'style': 'Regular'}
        fam = fn.get('family', 'Inter')
        style = fn.get('style', 'Regular')
        self.fonts_used.add((fam, style))
        wt = s.get('fontWeight')
        if not isinstance(wt, (int, float)):
            low = style.lower()
            wt = next((w for k, w in (('thin', 100), ('extra light', 200), ('extralight', 200), ('light', 300), ('medium', 500), ('semi bold', 600),
                                      ('semibold', 600), ('extra bold', 800), ('extrabold', 800), ('bold', 700), ('black', 900), ('heavy', 900)) if k in low), 400)
        sz = num(s.get('fontSize'), 16)
        lh = s.get('lineHeight') or {}
        lhs = size(rnd(lh['value'], 2), 'px') if lh.get('unit') == 'PIXELS' else (size(rnd(lh['value'] / 100, 3), 'em') if lh.get('unit') == 'PERCENT' else None)
        ls = s.get('letterSpacing') or {}
        lss = size(rnd(ls['value'], 2), 'px') if ls.get('value') and ls.get('unit') == 'PIXELS' else (
            size(rnd(ls['value'] / 100, 3), 'em') if ls.get('value') else None)
        tc = {'UPPER': 'uppercase', 'LOWER': 'lowercase', 'TITLE': 'capitalize'}.get(s.get('textCase'))
        fills = visible_paints(s.get('fills') if 'fills' in s else n.get('fills'))
        col = hexa(fills[-1]['color'], fills[-1].get('opacity', 1)) if fills and fills[-1].get('type') == 'SOLID' else '#000000'
        italic = 'italic' in style.lower()
        deco = {'UNDERLINE': 'underline', 'STRIKETHROUGH': 'line-through'}.get(s.get('textDecoration'))
        return dict(fam=fam, wt=int(wt), sz=sz, lh=lhs, ls=lss, tt=tc, col=col, italic=italic, deco=deco)

    def typo_key(self, st):
        key = f'{st["fam"]}|{st["sz"]}|{st["wt"]}|{json.dumps(st["lh"])}|{json.dumps(st["ls"])}|{st["tt"]}'
        self.ds.text_keys[key] = (st['fam'], st['sz'], st['wt'], st['lh'], st['ls'], st['tt'])
        return key

    def apply_typography(self, settings, st):
        key = self.typo_key(st)
        g = self.ds.typos.get(key)
        settings['typography_typography'] = 'custom'
        settings['typography_font_family'] = st['fam']
        settings['typography_font_size'] = size(st['sz'])
        settings['typography_font_weight'] = str(st['wt'])
        if st['lh']:
            settings['typography_line_height'] = st['lh']
        if st['ls']:
            settings['typography_letter_spacing'] = st['ls']
        if st['tt']:
            settings['typography_text_transform'] = st['tt']
        if st['italic']:
            settings['typography_font_style'] = 'italic'
        if st['deco']:
            settings['typography_text_decoration'] = st['deco']
        if g:
            settings.setdefault('__globals__', {})['typography_typography'] = f'globals/typography?id={g}'
        # responsive defaults (a design with real tablet/mobile frames overrides these)
        if st['sz'] >= 28:
            settings['typography_font_size_tablet'] = size(rnd(st['sz'] * 0.8))
            settings['typography_font_size_mobile'] = size(max(22, rnd(st['sz'] * 0.6)))

    # ---------- media
    def media_key(self, kind, ref, name):
        key = f'{kind}:{ref}'
        if key not in self.media:
            self.media[key] = {'ref': ref, 'kind': kind, 'alt': name or '', 'name': slug(name or ref)}
        return key

    def image_value(self, key, alt=''):
        return {'url': f'FTE_MEDIA:{key}', 'id': '', 'alt': alt, 'source': 'library'}

    # ---------- links (skill 145)
    def link_for(self, text, name=''):
        t = (text or name or '').strip().lower()
        t = re.sub(r'[^a-z0-9 ]+', '', t).strip()
        for label, target in self.link_map.items():
            if t == label.lower() or (len(t) > 3 and t.startswith(label.lower())):
                return target
        self.links_todo.append(text or name)
        return '#'

    def link_obj(self, url):
        return {'url': url, 'is_external': '', 'nofollow': '', 'custom_attributes': ''}

    # ---------- node -> Item
    def loop_item(self, n, lp):
        """Elementor Pro: repeated cards -> ONE Loop Grid widget (first card = Loop Item template with ACF dynamic tags)."""
        if lp['index'] != 0:
            return None
        it = self.container_item(n)
        slot_map = {}
        for kind, path, node in lp['slots']:
            name_key = lp['slot_map'].get(path)
            elid = self.el_by_node.get(id(dynamic.get_at(n, path)))
            if name_key and elid:
                slot_map[elid] = name_key
        dynamic.apply_dynamic(it.el, None, slot_map)
        it.el['settings'].pop('margin', None)
        self.loop_templates[lp['pt']] = it.el
        el = dynamic.loop_grid_widget('FTE_TEMPLATE:' + lp['pt'], lp['pt'], lp['columns'], lp['per_page'])
        el['settings']['_title'] = 'Loop Grid - ' + lp['pt']
        bx, by, bw, bh = lp['bbox']
        self.stats['loop_grid'] += 1
        return Item(bx, by, bw, bh, el, 'widget', n)

    def node_item(self, n, parent_n=None, section=False):
        lp = self.loops.get(id(n))
        if lp:
            return self.loop_item(n, lp)
        t = n.get('type')
        x, y, w, h = bbox(n)
        if 'asset' in n:
            return self.asset_item(n)
        if n.get('maskAsset'):
            return self.mask_item(n)
        if t == 'TEXT':
            return self.text_item(n)
        fills = visible_paints(n.get('fills'))
        children = kids(n)
        if not children:
            img = next((p for p in fills if p.get('type') == 'IMAGE'), None)
            if img:
                return self.image_fill_item(n, img)
            return self.shape_item(n)
        btn = self.button_item(n)
        if btn:
            return btn
        return self.container_item(n, section=section)

    def box_item(self, n, el, kind='widget'):
        x, y, w, h = bbox(n)
        return Item(x, y, w, h, el, kind, n)

    def widget(self, wtype, settings, n=None):
        settings.setdefault('_flex_size', 'none')   # never shrink: a shrunk flex item wraps text differently from the design
        el = {'id': new_id(), 'elType': 'widget', 'widgetType': wtype, 'settings': settings, 'elements': []}
        if n is not None:
            settings['_title'] = (n.get('name') or wtype)[:60]
            self.el_by_node[id(n)] = el['id']
        return el

    # ---- text
    def text_item(self, n):
        segs = n.get('segments') or []
        st = self.seg_style(n)
        st['col'] = with_alpha(st['col'], num(n.get('opacity'), 1.0))   # node opacity -> text colour alpha
        txt = n.get('characters') or ''
        x, y, w, h = bbox(n)
        name = n.get('name', '')
        multi_style = len({(s.get('fontName', {}).get('style'), s.get('fontSize'), json.dumps(s.get('fills'), sort_keys=True)) for s in segs}) > 1
        is_head = bool(re.match(r'^(h[1-6]|heading|title)\b', name, re.I)) or st['sz'] >= 26
        lines = max(1, round(h / max(1.0, (st['lh']['size'] if st['lh'] and st['lh']['unit'] == 'px' else st['sz'] * 1.25))))
        align = (n.get('textAlignHorizontal') or 'LEFT').lower().replace('justified', 'justify')
        settings = {'align': align}
        self.ds.add_color(st['col'])
        self.ds.add_text(self.typo_key(st))
        if is_head and not multi_style:
            tag = re.match(r'^h([1-6])\b', name, re.I)
            settings['title'] = re.sub(r'[\n  ]+', '<br>', self.esc(txt))
            settings['header_size'] = ('h' + tag.group(1)) if tag else ('h2' if st['sz'] >= 40 else ('h3' if st['sz'] >= 28 else 'h4'))
            self.color_setting(settings, 'title_color', st['col'])
            self.apply_typography(settings, st)
            el = self.widget('heading', settings, n)
        else:
            html_txt = self.rich_html(n, segs, st)
            settings['editor'] = html_txt
            self.color_setting(settings, 'text_color', st['col'])
            self.apply_typography(settings, st)
            el = self.widget('text-editor', settings, n)
        # width: a one-line text is left to size itself (it can never wrap); a wrapped paragraph keeps the design's width
        fixed_box = n.get('textAutoResize') in ('NONE', 'HEIGHT', 'TRUNCATE')    # auto-width text never wraps in Figma: leave it unconstrained
        if lines > 1 and fixed_box:
            settings['_element_width'] = 'initial'
            settings['_element_custom_width'] = size(rnd(w + max(6, w * 0.04)))   # browsers measure text a little wider than Figma
            settings['_element_custom_width_mobile'] = size(100, '%')
        elif align != 'left' and fixed_box:
            settings['_element_width'] = 'initial'      # alignment inside a fixed box needs the box
            settings['_element_custom_width'] = size(rnd(w))
            settings['_element_custom_width_mobile'] = size(100, '%')
        self.stats['text'] += 1
        it = self.box_item(n, el)
        it.node = n
        return it

    @staticmethod
    def esc(s):
        return (s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    def rich_html(self, n, segs, base):
        out = []
        for s in segs or [{'characters': n.get('characters', '')}]:
            t = self.esc(s.get('characters', '')).replace(' ', '\n').replace(' ', '\n')
            t = t.replace('\n', '<br>')
            fills = visible_paints(s.get('fills'))
            col = hexa(fills[-1]['color'], fills[-1].get('opacity', 1)) if fills and fills[-1].get('type') == 'SOLID' else base['col']
            fn = s.get('fontName') or {}
            wt = None
            low = (fn.get('style') or '').lower()
            if 'bold' in low or 'heavy' in low or 'black' in low:
                wt = 'bold'
            styles = []
            if col != base['col']:
                styles.append(f'color:{col}')
                self.ds.add_color(col)
            if s.get('fontSize') and abs(num(s['fontSize']) - base['sz']) > 0.5:
                styles.append(f'font-size:{rnd(s["fontSize"])}px')
            if wt and base['wt'] < 600:
                styles.append('font-weight:700')
            if s.get('textDecoration') == 'UNDERLINE' and base['deco'] != 'underline':
                styles.append('text-decoration:underline')
            link = s.get('hyperlink')
            if isinstance(link, dict) and link.get('value'):
                t = f'<a href="{self.esc(link["value"])}">{t}</a>'
            out.append(f'<span style="{";".join(styles)}">{t}</span>' if styles else t)
        return ''.join(out)

    # ---- images / assets / shapes
    def svg_is_plain_rect(self, a, w, h):
        """A vector that is only a straight-edged rectangle (M/L/Z) can be a native container with the fill instead of an SVG."""
        if a.get('format') != 'svg':
            return False
        try:
            txt = (self.dir / a['path']).read_text('utf-8')
        except OSError:
            return False
        if txt.count('<path') != 1 or re.search(r'<path[^>]* d="[^"]*[CcQqAaSsTt]', txt):
            return False
        d = re.search(r' d="([^"]*)"', txt)
        pts = re.findall(r'(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)', d.group(1)) if d else []
        if not 4 <= len(pts) <= 5:
            return False
        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
        return abs((max(xs) - min(xs)) - w) < 1.5 and abs((max(ys) - min(ys)) - h) < 1.5

    def asset_item(self, n):
        a = n['asset']
        x, y, w, h = bbox(n)
        if n.get('type') in ('VECTOR', 'RECTANGLE') and not visible_paints(n.get('strokes')) and self.svg_is_plain_rect(a, w, h) \
                and all(p.get('type') != 'IMAGE' for p in visible_paints(n.get('fills'))) and visible_paints(n.get('fills')):
            self.stats['rect_vector_to_native'] += 1
            return self.shape_item(n)
        rb = a.get('renderBounds') or {'x': x, 'y': y, 'width': w, 'height': h}
        key = self.media_key('svg' if a['format'] == 'svg' else 'image', a['path'], n.get('name'))
        s = {'image': self.image_value(key, n.get('name', '')), 'image_size': 'full',
             'width': size(rnd(rb['width'])), 'width_mobile': size(min(100, 100), '%') if rb['width'] > 600 else size(rnd(rb['width'])),
             'align': 'left'}
        el = self.widget('image', s, n)
        it = Item(rb['x'], rb['y'], rb['width'], rb['height'], el, 'widget', n)
        self.stats['asset'] += 1
        return it

    def mask_item(self, n):
        """Figma mask: no native Elementor equivalent. The group is rendered (with its mask) from the exact HTML to a transparent
        image and used as a normal image widget. Without a render the content is kept unmasked and reported."""
        png = self.raster.get(n.get('id'))
        x, y, w, h = bbox(n)
        if png:
            rel = str(Path(png).resolve().relative_to(self.dir.resolve())).replace('\\', '/') if str(png).startswith(str(self.dir)) else str(png)
            key = self.media_key('image', rel, n.get('name'))
            s = {'image': self.image_value(key, n.get('name', '')), 'image_size': 'full', 'width': size(rnd(w)), 'height': size(rnd(h)),
                 'object-fit': 'fill', 'width_mobile': size(100, '%'), 'align': 'left'}
            self.stats['mask_raster'] += 1
            return Item(x, y, w, h, self.widget('image', s, n), 'widget', n)
        self.warn['masked groups rendered without mask'] += 1
        return self.container_item({**n, 'maskAsset': None}, section=False)

    def image_fill_item(self, n, img):
        info = self.images.get(img.get('imageHash') or '')
        if not info:
            self.warn['missing image'] += 1
            return self.shape_item(n)
        x, y, w, h = bbox(n)
        key = self.media_key('image', info['path'], n.get('name'))
        s = {'image': self.image_value(key, n.get('name', '')), 'image_size': 'full', 'width': size(rnd(w)), 'height': size(rnd(h)),
             'object-fit': 'cover', 'width_mobile': size(100, '%'), 'height_mobile': size(rnd(h * 0.6)), 'align': 'left'}
        r = self.radius(n)
        if r:
            s['image_border_radius'] = r
        el = self.widget('image', s, n)
        self.stats['image'] += 1
        return Item(x, y, w, h, el, 'widget', n)

    def radius(self, n):
        cr = n.get('cornerRadius')
        if cr == MIXED:
            vals = [num(n.get(k)) for k in ('topLeftRadius', 'topRightRadius', 'bottomRightRadius', 'bottomLeftRadius')]
            return dims(*vals) if any(vals) else None
        if num(cr) > 0:
            return dims(num(cr))
        if n.get('type') == 'ELLIPSE':
            return dims(max(num(n.get('width')), num(n.get('height'))), unit='px')
        return None

    def shape_item(self, n):
        """Leaf rectangle/ellipse: an empty native container with a fixed size and the fill."""
        x, y, w, h = bbox(n)
        s = self.container_style(n)
        s['width'] = size(rnd(w))
        s['min_height'] = size(rnd(h))
        s['content_width'] = 'full'
        el = {'id': new_id(), 'elType': 'container', 'isInner': True, 'settings': s, 'elements': []}
        s['_title'] = (n.get('name') or 'Shape')[:60]
        self.stats['shape'] += 1
        return Item(x, y, w, h, el, 'container', n)

    # ---- container styles
    def container_style(self, n):
        s = {}
        fills = visible_paints(n.get('fills'))
        solid = [p for p in fills if p.get('type') == 'SOLID']
        grad = [p for p in fills if p.get('type', '').startswith('GRADIENT')]
        img = [p for p in fills if p.get('type') == 'IMAGE']
        if img:
            info = self.images.get(img[0].get('imageHash') or '')
            if info:
                key = self.media_key('image', info['path'], n.get('name'))
                s['background_background'] = 'classic'
                s['background_image'] = self.image_value(key, n.get('name', ''))
                s['background_position'] = 'center center'
                s['background_repeat'] = 'no-repeat'
                s['background_size'] = 'cover'
        elif grad and grad[-1].get('type') == 'GRADIENT_LINEAR' and len(grad[-1]['gradientStops']) >= 2:
            g = grad[-1]
            st = g['gradientStops']
            if len(st) > 2:   # Elementor's native gradient has 2 colours: keep the first and last stop
                self.warn['gradients with >2 stops reduced to 2'] += 1
                st = [st[0], st[-1]]
            s['background_background'] = 'gradient'
            s['background_color'] = hexa(st[0]['color'], g.get('opacity', 1))
            s['background_color_b'] = hexa(st[1]['color'], g.get('opacity', 1))
            s['background_color_stop'] = size(rnd(st[0]['position'] * 100), '%')
            s['background_color_b_stop'] = size(rnd(st[1]['position'] * 100), '%')
            s['background_gradient_type'] = 'linear'
            T = g.get('gradientTransform') or [[1, 0, 0], [0, 1, 0]]
            # direction vector of the gradient in node space = inverse transform of (1,0)
            det = T[0][0] * T[1][1] - T[0][1] * T[1][0] or 1
            dx, dy = T[1][1] / det, -T[1][0] / det
            s['background_gradient_angle'] = {'unit': 'deg', 'size': rnd((math.degrees(math.atan2(dx, -dy))) % 360), 'sizes': []}
        elif solid:
            col = hexa(solid[-1]['color'], solid[-1].get('opacity', 1))
            s['background_background'] = 'classic'
            self.color_setting(s, 'background_color', col)
            self.ds.add_color(col, 2)
        r = self.radius(n)
        if r:
            s['border_radius'] = r
        strokes = visible_paints(n.get('strokes'))
        if strokes and strokes[-1].get('type') == 'SOLID' and num(n.get('strokeWeight')) > 0:
            col = hexa(strokes[-1]['color'], strokes[-1].get('opacity', 1))
            wgt = n.get('strokeWeight', 1)
            s['border_border'] = 'dashed' if n.get('dashPattern') else 'solid'
            s['border_width'] = dims(*(num(n.get(k, wgt)) for k in ('strokeTopWeight', 'strokeRightWeight', 'strokeBottomWeight', 'strokeLeftWeight'))) \
                if n.get('strokeTopWeight') is not None else dims(num(wgt))
            self.color_setting(s, 'border_color', col)
            self.ds.add_color(col)
        for e in n.get('effects') or []:
            if e.get('type') == 'DROP_SHADOW' and e.get('visible', True):
                o = e.get('offset') or {'x': 0, 'y': 0}
                s['box_shadow_box_shadow_type'] = 'yes'
                s['box_shadow_box_shadow'] = {'horizontal': rnd(o['x']), 'vertical': rnd(o['y']), 'blur': rnd(num(e.get('radius'))),
                                              'spread': rnd(num(e.get('spread'))), 'color': hexa(e.get('color') or {'r': 0, 'g': 0, 'b': 0, 'a': .25})}
                break
        if n.get('clipsContent') or (r and kids(n)):
            s['overflow'] = 'hidden'
        return s

    # ---- button
    def button_item(self, n):
        ch = kids(n)
        if len(ch) != 1 or ch[0].get('type') != 'TEXT':
            return None
        name = n.get('name', '')
        x, y, w, h = bbox(n)
        fills = [p for p in visible_paints(n.get('fills')) if p.get('type') == 'SOLID']
        strokes = visible_paints(n.get('strokes'))
        looks = bool(fills or strokes) and h <= 120 and w <= 520 and ('asset' not in ch[0])
        if not (looks and (re.search(r'btn|button|cta', name, re.I) or (n.get('layoutMode') and w < 400))):
            return None
        t = ch[0]
        st = self.seg_style(t)
        tx, ty, tw, th = bbox(t)
        s = {'text': (t.get('characters') or '').strip(), 'align': 'left', 'link': self.link_obj(self.link_for(t.get('characters'), name))}
        self.apply_typography(s, st)
        self.ds.add_text(self.typo_key(st))
        self.ds.add_color(st['col'])
        self.color_setting(s, 'button_text_color', st['col'])
        if fills:
            col = hexa(fills[-1]['color'], fills[-1].get('opacity', 1))
            self.color_setting(s, 'background_color', col)
            self.ds.add_color(col, 2)
        r = self.radius(n)
        if r:
            s['border_radius'] = r
        if strokes and strokes[-1].get('type') == 'SOLID':
            s['border_border'] = 'solid'
            s['border_width'] = dims(num(n.get('strokeWeight', 1)))
            self.color_setting(s, 'border_color', hexa(strokes[-1]['color']))
        pl, pt = max(0, tx - x), max(0, ty - y)
        pr, pb = max(0, x + w - (tx + tw)), max(0, y + h - (ty + th))
        s['text_padding'] = dims(pt, pr, pb, pl)
        s['_element_width'] = 'initial'
        s['_element_custom_width'] = size(rnd(w))
        el = self.widget('button', s, n)
        self.stats['button'] += 1
        return Item(x, y, w, h, el, 'widget', n)

    # ---- container (frame with children)
    def exact_container(self, n):
        """Pixel-exact: a fixed-size container; every child sits at its exact x/y (Elementor native Position: Absolute)."""
        x, y, w, h = bbox(n)
        style = self.container_style(n)
        items = []
        for z, c in enumerate(kids(n), 1):
            it = self.node_item(c, n)
            if it:
                it.z = z
                items.append(it)
        items = self.take_backdrops(items, (x, y, w, h), style)
        s = dict(style)
        s['content_width'] = 'full'
        s['width'] = size(rnd(w))
        s['min_height'] = size(rnd(h))
        s['padding'] = dims(0)
        s['_title'] = (n.get('name') or 'Container')[:60]
        els = []
        for it in items:
            els.append(self.make_absolute(it, (x, y)))
            it.el['settings']['_z_index' if it.el['elType'] == 'widget' else 'z_index'] = it.z
        el = {'id': new_id(), 'elType': 'container', 'isInner': True, 'settings': s, 'elements': els}
        self.stats['exact_container'] += 1
        return Item(x, y, w, h, el, 'container', n)

    def container_item(self, n, section=False):
        if self.exact:
            return self.exact_container(n)
        x, y, w, h = bbox(n)
        style = self.container_style(n)
        ch = kids(n)
        if n.get('layoutMode') in ('HORIZONTAL', 'VERTICAL'):
            items = []
            absolute = []
            for c in ch:
                it = self.node_item(c, n)
                if not it:
                    continue
                (absolute if c.get('layoutPositioning') == 'ABSOLUTE' else items).append(it)
            row = n['layoutMode'] == 'HORIZONTAL'
            s = dict(style)
            # absolute children that cover the whole frame (background rectangles / photos) become the container background
            if absolute:
                rest = self.take_backdrops(absolute, (x, y, w, h), s)
                absolute = rest
            s['content_width'] = 'full'
            if 'background_image' in s or any(k.startswith('background_overlay') for k in s):
                s['min_height'] = size(rnd(h))
            s['flex_direction'] = 'row' if row else 'column'
            pa = {'MIN': 'flex-start', 'CENTER': 'center', 'MAX': 'flex-end', 'SPACE_BETWEEN': 'space-between'}.get(n.get('primaryAxisAlignItems'), 'flex-start')
            ca = {'MIN': 'flex-start', 'CENTER': 'center', 'MAX': 'flex-end', 'BASELINE': 'flex-start'}.get(n.get('counterAxisAlignItems'), 'flex-start')
            s['flex_justify_content'] = pa
            s['flex_align_items'] = ca
            gap = num(n.get('itemSpacing'))
            s['flex_gap'] = {'column': str(rnd(gap)), 'row': str(rnd(gap)), 'isLinked': True, 'unit': 'px', 'size': rnd(gap)}
            if n.get('layoutWrap') == 'WRAP':
                s['flex_wrap'] = 'wrap'
            s['padding'] = dims(num(n.get('paddingTop')), num(n.get('paddingRight')), num(n.get('paddingBottom')), num(n.get('paddingLeft')))
            self.fix_child_widths(items, n, row)
            els = [i.el for i in items]
            for a in absolute:
                els.append(self.make_absolute(a, (x, y)))
            s['_title'] = (n.get('name') or 'Container')[:60]
            el = {'id': new_id(), 'elType': 'container', 'isInner': True, 'settings': s, 'elements': els}
            self.stats['autolayout'] += 1
            return Item(x, y, w, h, el, 'container', n)
        return self.arranged_container(n, (x, y, w, h), style, ch)

    def fix_child_widths(self, items, parent, row):
        for it in items:
            lm = it.node.get('layoutSizingHorizontal') if it.node else None
            if lm == 'FILL' and row is False:
                it.el['settings']['width'] = size(100, '%')
            elif lm == 'FILL' and row:
                it.el['settings']['_flex_size'] = 'grow'
            if it.kind == 'container' and it.node.get('layoutSizingHorizontal') == 'FIXED':
                it.el['settings'].setdefault('width', size(rnd(it.w)))

    def make_absolute(self, it, origin):
        s = it.el['settings']
        key = '_position' if it.el['elType'] == 'widget' else 'position'
        s[key] = 'absolute'
        s['_offset_orientation_h'] = 'start'
        s['_offset_x'] = size(rnd(it.x - origin[0]))
        s['_offset_orientation_v'] = 'start'
        s['_offset_y'] = size(rnd(it.y - origin[1]))
        if it.el['elType'] == 'container':
            s['width'] = size(rnd(it.w))
        return it.el

    def arranged_container(self, n, box, style, ch):
        """A frame without auto-layout: infer rows / columns from geometry."""
        bx, by, bw, bh = box
        items = []
        for z, c in enumerate(ch, 1):
            it = self.node_item(c, n)
            if it:
                it.z = z
                items.append(it)
        s = dict(style)
        s['content_width'] = 'full'
        s['_title'] = (n.get('name') or 'Container')[:60]
        if not items:
            s['width'] = size(rnd(bw))
            s['min_height'] = size(rnd(bh))
            el = {'id': new_id(), 'elType': 'container', 'isInner': True, 'settings': s, 'elements': []}
            return Item(bx, by, bw, bh, el, 'container', n)
        items = self.take_backdrops(items, box, s)
        if not items:
            s['width'] = size(rnd(bw))
            s['min_height'] = size(rnd(bh))
            el = {'id': new_id(), 'elType': 'container', 'isInner': True, 'settings': s, 'elements': []}
            return Item(bx, by, bw, bh, el, 'container', n)
        flow, decor = self.split_decor(items, box)
        parent_has_visual = any(k.startswith(('background', 'border', 'box_shadow')) for k in s)
        child_has_border = any(k.startswith(('border', 'box_shadow')) for k in flow[0].el['settings']) if flow else False
        if len(flow) == 1 and not decor and flow[0].kind == 'container' and flow[0].w >= 0.95 * bw and flow[0].h >= 0.9 * bh \
                and not child_has_border and not (parent_has_visual and any(k.startswith('background') for k in flow[0].el['settings'])):
            # a lone full-size wrapper container adds nothing: merge it into this one (padding adds up, its background moves up)
            c = flow[0]
            cs = c.el['settings']
            for k, v in cs.items():
                if k.startswith(('background', 'overflow', 'min_height')) or k == '__globals__':
                    if k == '__globals__':
                        s.setdefault('__globals__', {}).update(v)
                    else:
                        s[k] = v
            pt, pr, pb, pl = (max(0, c.y - by), max(0, bx + bw - (c.x + c.w)), max(0, by + bh - (c.y + c.h)), max(0, c.x - bx))
            cp = cs.get('padding') or dims(0)
            s.update({k: v for k, v in cs.items() if k in ('flex_direction', 'flex_gap', 'flex_align_items', 'flex_justify_content', 'flex_wrap',
                                                             'flex_direction_mobile', 'margin')})
            s['padding'] = dims(pt + int(cp['top']), pr + int(cp['right']), pb + int(cp['bottom']), pl + int(cp['left']))
            els = c.el['elements']
            for k in ('background_image', 'background_overlay_background'):
                pass
            self.stats['merged_wrapper'] += 1
            el = {'id': new_id(), 'elType': 'container', 'isInner': True, 'settings': s, 'elements': els}
            return Item(bx, by, bw, bh, el, 'container', n)
        els, cont = self.arrange(flow, box)
        s.update(cont)
        if 'background_image' in s or any(k.startswith('background_overlay') for k in s):
            s['min_height'] = size(rnd(bh))
        for d in decor:
            els.append(self.make_absolute(d, (bx, by)))
        if decor:  # keep the Figma paint order: absolute layers must not cover (or hide behind) the wrong siblings
            for it in flow + decor:
                it.el['settings']['_z_index' if it.el['elType'] == 'widget' else 'z_index'] = it.z
        el = {'id': new_id(), 'elType': 'container', 'isInner': True, 'settings': s, 'elements': els}
        self.stats['inferred'] += 1
        return Item(bx, by, bw, bh, el, 'container', n)

    # ---- backdrops: a big image / rectangle behind the content becomes the container background (native)
    @staticmethod
    def _walk_els(el):
        yield el
        for c in el.get('elements', []):
            yield from Builder._walk_els(c)

    def _pure_visual(self, it):
        """True when the item has no text/button inside (only shapes/images)."""
        for e in self._walk_els(it.el):
            if e.get('elType') == 'widget' and e.get('widgetType') in ('heading', 'text-editor', 'button'):
                return False
        return True

    def take_backdrops(self, items, box, style):
        bx, by, bw, bh = box
        area = max(1.0, bw * bh)
        tw, th, ta = (0.985, 0.985, 0.97) if self.exact else (0.9, 0.85, 0.85)   # pixel-exact: only a true full-size layer is a background
        back = [it for it in items if it.w * it.h >= ta * area and it.w >= tw * bw and it.h >= th * bh and self._pure_visual(it)
                and not (it.el['elType'] == 'widget' and it.el['widgetType'] == 'image' and it.node.get('asset', {}).get('format') == 'svg')]
        if not back or len(back) == len(items):
            return items
        for it in back:
            imgs = [e for e in self._walk_els(it.el) if e.get('widgetType') == 'image' and e['settings'].get('image', {}).get('url', '').startswith('FTE_MEDIA:')]
            if imgs and 'background_image' not in style:
                img = imgs[0]['settings']['image']
                style['background_background'] = 'classic'
                style['background_image'] = {'url': img['url'], 'id': '', 'alt': img.get('alt', ''), 'source': 'library'}
                style['background_position'] = 'center center'
                style['background_repeat'] = 'no-repeat'
                style['background_size'] = 'cover'
                self.stats['backdrop_image'] += 1
                continue
            st = it.el['settings'] if it.el['elType'] == 'container' else {}
            bgk = {k: v for k, v in st.items() if k.startswith('background_') or k == '__globals__'}
            if not bgk:
                continue
            if 'background_background' not in style and 'background_image' not in style:
                for k, v in bgk.items():
                    if k == '__globals__':
                        style.setdefault('__globals__', {}).update(v)
                    else:
                        style[k] = v
                self.stats['backdrop_fill'] += 1
            else:  # a second layer on top of an image/fill -> overlay (native Elementor background overlay)
                for k, v in bgk.items():
                    if k == '__globals__':
                        style.setdefault('__globals__', {}).update({kk.replace('background_', 'background_overlay_'): vv for kk, vv in v.items()})
                    else:
                        style[k.replace('background_', 'background_overlay_', 1)] = v
                self.stats['backdrop_overlay'] += 1
        return [it for it in items if it not in back]

    def split_decor(self, items, box):
        """Items that overlap other items (big ghost text, waves, overlays) become absolute decoration."""
        if len(items) < 2:
            return items, []
        flow, decor = [], []
        for it in items:
            others = [o for o in items if o is not it]
            ov = [o for o in others if h_overlap(it, o) > 0 and v_overlap(it, o) > 0]
            heavy = [o for o in ov if (min(h_overlap(it, o), it.w) * min(v_overlap(it, o), it.h)) > 0.35 * min(it.w * it.h, o.w * o.h)]
            if heavy and (it.w * it.h) >= max(o.w * o.h for o in heavy) * 0.999 and it.kind != 'widget':
                decor.append(it)
            elif heavy and it.kind == 'widget' and it.node.get('type') != 'TEXT':
                decor.append(it)
            else:
                flow.append(it)
        if not flow:  # everything overlaps: keep the first as flow
            flow, decor = decor[:1], decor[1:]
        return flow, decor

    @staticmethod
    def cut(items, axis, tol=1.0):
        """Whitespace cut (XY-cut): split items into groups separated by a clean gap along the axis."""
        key0, key1 = ('y', 'b') if axis == 'y' else ('x', 'r')
        its = sorted(items, key=lambda i: (getattr(i, key0), getattr(i, key1)))
        groups, cur, edge = [], [its[0]], getattr(its[0], key1)
        for it in its[1:]:
            if getattr(it, key0) >= edge - tol:
                groups.append(cur)
                cur, edge = [it], getattr(it, key1)
            else:
                cur.append(it)
                edge = max(edge, getattr(it, key1))
        groups.append(cur)
        return groups

    def group_item(self, items):
        """A set of items that belong together -> one Item (a container when there is more than one)."""
        if len(items) == 1:
            return items[0]
        box = union(items)
        els, s = self.arrange(items, box)
        s['padding'] = dims(0)
        s['content_width'] = 'full'
        s['_title'] = 'Group'
        el = {'id': new_id(), 'elType': 'container', 'isInner': True, 'settings': s, 'elements': els}
        it = Item(box[0], box[1], box[2], box[3], el, 'container')
        it.z = min(i.z for i in items)
        return it

    def arrange(self, items, box):
        """items -> (child elements, container settings). Recursive XY-cut: rows first, then columns."""
        bx, by, bw, bh = box
        ux, uy, uw, uh = union(items)
        pad = (max(0, uy - by), max(0, bx + bw - (ux + uw)), max(0, by + bh - (uy + uh)), max(0, ux - bx))
        s = {'padding': dims(*[rnd(p) for p in pad])}
        ubox = (ux, uy, uw, uh)
        if len(items) == 1:
            return self.column_from_items(items, ubox, s)
        ygroups = self.cut(items, 'y')
        if len(ygroups) > 1:
            return self.column_from_items([self.group_item(g) for g in ygroups], ubox, s)
        xgroups = self.cut(items, 'x')
        if len(xgroups) > 1:
            return self.arrange_row([self.group_item(g) for g in xgroups], ubox, s)
        return self.arrange_row(sorted(items, key=lambda i: i.x), ubox, s)   # overlapping leftovers: one row, reading order

    def finish_widths(self, it, parent_w, in_row):
        st = it.el['settings']
        if it.el['elType'] == 'container':
            st.setdefault('width', size(rnd(it.w)))
            st['width_mobile'] = size(100, '%')
            if in_row:
                st.setdefault('flex_wrap_mobile', 'wrap')
        elif it.el['widgetType'] in ('image',):
            pass

    def arrange_row(self, row, ubox, s):
        ux, uy, uw, uh = ubox
        s['flex_direction'] = 'row'
        s['flex_direction_mobile'] = 'column'
        s['flex_wrap'] = 'nowrap'
        gaps = [row[i + 1].x - row[i].r for i in range(len(row) - 1)]
        gap = mode_gap(gaps)
        s['flex_gap'] = {'column': str(max(0, gap)), 'row': str(max(0, gap)), 'isLinked': True, 'unit': 'px', 'size': max(0, gap)}
        # vertical alignment
        tops = [i.y for i in row]
        cys = [i.cy for i in row]
        bots = [i.b for i in row]
        if max(tops) - min(tops) <= 2:
            s['flex_align_items'] = 'flex-start'
        elif max(cys) - min(cys) <= 3:
            s['flex_align_items'] = 'center'
        elif max(bots) - min(bots) <= 2:
            s['flex_align_items'] = 'flex-end'
        else:
            s['flex_align_items'] = 'flex-start'
            for i in row:
                self.set_margin(i, top=i.y - uy)
        s['flex_justify_content'] = 'flex-start'
        for k, i in enumerate(row):
            if k > 0 and abs((i.x - row[k - 1].r) - gap) > 2:
                self.set_margin(i, left=(i.x - row[k - 1].r) - gap)
            self.finish_widths(i, uw, True)
        s['_title_hint'] = 'row'
        s.pop('_title_hint')
        return [i.el for i in row], s

    def arrange_column(self, rows, ubox, s):
        row_items = []
        for row in rows:
            if len(row) == 1:
                row_items.append(row[0])
            else:
                row_items.append(self.group_item(row))
        return self.column_from_items(row_items, ubox, s)

    def column_from_items(self, row_items, ubox, s):
        ux, uy, uw, uh = ubox
        s['flex_direction'] = 'column'
        gaps = [row_items[i + 1].y - row_items[i].b for i in range(len(row_items) - 1)]
        gap = mode_gap(gaps)
        s['flex_gap'] = {'column': str(max(0, gap)), 'row': str(max(0, gap)), 'isLinked': True, 'unit': 'px', 'size': max(0, gap)}
        lefts = [i.x for i in row_items]
        cxs = [i.cx for i in row_items]
        rights = [i.r for i in row_items]
        cx0 = ux + uw / 2
        if max(lefts) - min(lefts) <= 2:
            s['flex_align_items'] = 'flex-start'
        elif max(abs(c - cx0) for c in cxs) <= 3:
            s['flex_align_items'] = 'center'
        elif max(rights) - min(rights) <= 2:
            s['flex_align_items'] = 'flex-end'
        else:
            s['flex_align_items'] = 'flex-start'
            for i in row_items:
                self.set_margin(i, left=i.x - ux)
        s['flex_justify_content'] = 'flex-start'
        for k, i in enumerate(row_items):
            if k > 0 and abs((i.y - row_items[k - 1].b) - gap) > 2:
                self.set_margin(i, top=(i.y - row_items[k - 1].b) - gap)
            self.finish_widths(i, uw, False)
        return [i.el for i in row_items], s

    def arrange_row_of_stacks(self, cols, ubox, s):
        ux, uy, uw, uh = ubox
        stacks = []
        for col in cols:
            if len(col) == 1:
                stacks.append(col[0])
                continue
            cx, cy, cw, ch_ = union(col)
            rows = cluster_rows(col)
            cs = {'padding': dims(0), 'content_width': 'full', '_title': 'Column'}
            els, cs2 = self.arrange_column(rows, (cx, cy, cw, ch_), cs) if len(rows) > 1 else self.arrange_row(rows[0], (cx, cy, cw, ch_), cs)
            cs2['padding'] = dims(0)
            cs2['width'] = size(rnd(cw))
            cs2['width_mobile'] = size(100, '%')
            el = {'id': new_id(), 'elType': 'container', 'isInner': True, 'settings': cs2, 'elements': els}
            stacks.append(Item(cx, cy, cw, ch_, el, 'container'))
        return self.arrange_row(stacks, ubox, s)

    @staticmethod
    def set_margin(it, top=None, left=None):
        key = '_margin' if it.el['elType'] == 'widget' else 'margin'
        cur = it.el['settings'].get(key) or dims(0)
        t, l = int(cur['top']), int(cur['left'])
        if top is not None:
            t = rnd(top)
        if left is not None:
            l = rnd(left)
        it.el['settings'][key] = dims(t, int(cur['right']), int(cur['bottom']), l)

    def finalize(self, el, avail):
        """Flex hygiene: fixed-size containers never shrink; anything wider than its parent becomes 100%."""
        s = el['settings']
        if self.exact:
            return
        if el['elType'] == 'widget':
            cw = s.get('_element_custom_width')
            if cw and avail and cw['size'] > avail + 2:
                s['_element_custom_width'] = size(100, '%')
            w = s.get('width')
            if w and w.get('unit') == 'px' and avail and w['size'] > avail + 2:
                s['width'] = size(100, '%')
            return
        own = avail
        w = s.get('width')
        if w and w.get('unit') == 'px':
            if avail and w['size'] > avail + 2:
                s['width'] = size(100, '%')
            else:
                if s.get('position') != 'absolute':
                    s.setdefault('_flex_size', 'none')
                own = w['size']
        if s.get('content_width') == 'boxed' and not (w and w.get('unit') == 'px'):
            own = self.container_w
        pad = s.get('padding')
        if own and pad:
            own = own - int(pad['left']) - int(pad['right'])
        for c in el.get('elements', []):
            self.finalize(c, own)

    # ---------- page
    def build_page(self, root, sections_only=True):
        """root frame -> list of top-level Elementor containers (one per section, ordered by y)."""
        W = root.get('width', 1440)
        top = sorted(kids(root), key=lambda c: (bbox(c)[1], bbox(c)[0]))
        self.section_nodes = top
        # global content width: the most common section width that is narrower than the page, else the page width
        widths = [round(bbox(c)[2]) for c in top if bbox(c)[3] > 60]
        content_ws = [w for w in widths if w < W - 40]
        self.container_w = Counter(content_ws).most_common(1)[0][0] if content_ws else (Counter(widths).most_common(1)[0][0] if widths else rnd(W))
        out = []
        order = {id(c): i for i, c in enumerate(kids(root), 1)}   # Figma paint order (1 = back)
        prev_bottom = None
        for sec in top:
            el = self.section(sec, root)
            x, y, w, h = bbox(sec)
            if prev_bottom is not None:
                gap = rnd(y - prev_bottom)        # gap between sections (negative = overlap, e.g. a hero tucked under the header)
                if gap:
                    el['settings']['margin'] = dims(gap, 0, 0, 0)
            el['settings']['z_index'] = order.get(id(sec), 0)
            prev_bottom = y + h
            out.append(el)
        for el in out:
            self.finalize(el, W)
        return out

    def exact_section(self, sec, root):
        x, y, w, h = bbox(sec)
        W = root.get('width', 1440)
        full_bleed = w >= W - 40
        inner = self.exact_container(sec).el
        st = inner['settings']
        outer = {'id': new_id(), 'elType': 'container', 'isInner': False, 'elements': [inner], 'settings': {}}
        os_ = outer['settings']
        os_['content_width'] = 'full'
        os_['flex_direction'] = 'column'
        os_['flex_align_items'] = 'center'
        os_['padding'] = dims(0)
        os_['min_height'] = size(rnd(h))
        os_['_title'] = (sec.get('name') or 'Section')[:60]
        if full_bleed:   # background to the screen edge
            for k in list(st.keys()):
                if k.startswith(('background_', 'border_', 'box_shadow')) or k == '__globals__':
                    if k == '__globals__':
                        g = {kk: vv for kk, vv in st['__globals__'].items() if kk.startswith(('background_', 'border_'))}
                        if g:
                            os_.setdefault('__globals__', {}).update(g)
                            for kk in g:
                                st['__globals__'].pop(kk)
                        continue
                    os_[k] = st.pop(k)
        st['_title'] = 'Container'
        return outer

    def section(self, sec, root):
        if self.exact:
            return self.exact_section(sec, root)
        x, y, w, h = bbox(sec)
        W = root.get('width', 1440)
        full_bleed = w >= W - 40
        # 1) convert the section's own content as a normal container tree
        inner_item = self.container_item(sec, section=True)
        inner = inner_item.el
        st = inner['settings']
        outer = {'id': new_id(), 'elType': 'container', 'isInner': False, 'elements': [], 'settings': {}}
        os_ = outer['settings']
        os_['content_width'] = 'full'
        os_['_title'] = (sec.get('name') or 'Section')[:60]
        # full-bleed sections: the background goes to the outer (Full Width) container; narrower panels keep their own style
        for k in list(st.keys()) if full_bleed else []:
            if k.startswith(('background_', 'border_', 'box_shadow')) or k in ('__globals__',):
                if k == '__globals__':
                    g = {kk: vv for kk, vv in st['__globals__'].items() if kk.startswith(('background_', 'border_'))}
                    if g:
                        os_.setdefault('__globals__', {}).update(g)
                        for kk in g:
                            st['__globals__'].pop(kk)
                    continue
                os_[k] = st.pop(k)
        if 'background_image' in os_ or any(k.startswith('background_overlay') for k in os_):
            os_['min_height'] = size(rnd(h))
        if not full_bleed:
            os_['flex_align_items'] = 'center'
            st['width'] = size(rnd(w))
        # 2) inner boxed container on the global width
        inner['isInner'] = True
        pad = st.pop('padding', dims(0))
        top_b = (int(pad['top']), int(pad['bottom']))
        # horizontal: when the content is narrower/wider than the global width, add the difference as padding
        gw = self.container_w
        left_pad, right_pad = int(pad['left']), int(pad['right'])
        if full_bleed:
            side = max(0, (W - gw) / 2)
            left_pad = max(0, left_pad - side)
            right_pad = max(0, right_pad - side)
        else:
            left_pad = right_pad = 0
        os_['padding'] = dims(top_b[0], 0, top_b[1], 0)
        st['padding'] = dims(0, rnd(right_pad), 0, rnd(left_pad))
        st['content_width'] = 'boxed'
        if full_bleed:
            st.pop('width', None)
        st.pop('min_height', None) if full_bleed else None
        st['_title'] = 'Container'
        outer['elements'] = [inner]
        os_['padding'] = dims(top_b[0], 0, top_b[1], 0)
        return outer
