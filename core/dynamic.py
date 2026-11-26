"""
dynamic.py - make the site DYNAMIC from the design (ACF PRO + Elementor PRO):

  repeated cards in the Figma frame  ->  Custom Post Type + ACF field group + one post per card
                                      ->  Loop Item template (Elementor Pro) with ACF dynamic tags
                                      ->  Loop Grid widget in the page (replaces the static cards)

Without Elementor Pro the CPT + ACF + posts are still created (content becomes editable in WordPress) and the cards stay
as native static containers. Elementor Pro parts (Loop Item / Loop Grid / dynamic tags) are generated from Elementor's
documented JSON format but are UNTESTED here (no Pro licence on the test site) - the generator marks them `pro: true`.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from collections import Counter


def slug(s, n=32):
    s = re.sub(r'[^a-zA-Z0-9]+', '_', str(s or '')).strip('_').lower()[:n].strip('_')
    return s or 'item'


def kids(n):
    return [c for c in n.get('children') or [] if c.get('visible') is not False and not c.get('isMask')]


def sig(n, depth=3):
    """Structural signature: node type + child signatures (depth-limited)."""
    t = n.get('type')
    if 'asset' in n:
        return 'A'
    if t == 'TEXT':
        return 'T'
    ch = kids(n)
    if not ch:
        return 'I' if any(p.get('type') == 'IMAGE' for p in (n.get('fills') or []) if p.get('visible', True)) else 'S'
    if depth == 0:
        return 'F'
    return 'F(' + ','.join(sig(c, depth - 1) for c in ch) + ')'


def similar_size(a, b, tol=0.12):
    return abs(a['width'] - b['width']) <= tol * max(a['width'], 1) and abs(a['height'] - b['height']) <= tol * max(a['height'], 1)


def find_repeat_groups(node, min_count=3, min_slots=2):
    """Yield (parent_node, [card nodes]) where >= min_count siblings share a structure."""
    out = []

    def walk(n):
        ch = kids(n)
        if len(ch) >= min_count:
            groups = {}
            for c in ch:
                if c.get('type') in ('FRAME', 'INSTANCE', 'COMPONENT', 'GROUP') and kids(c) and 'asset' not in c:
                    groups.setdefault(sig(c), []).append(c)
            for s, cards in groups.items():
                if len(cards) >= min_count and all(similar_size(cards[0], c) for c in cards) and len(slots(cards[0])) >= min_slots:
                    out.append((n, cards))
        for c in ch:
            walk(c)
    walk(node)
    # keep outermost groups only (a group inside another group's card is a sub-structure)
    ids = {id(c) for _, cards in out for c in cards}
    return [(p, cards) for p, cards in out if not _inside(p, ids)]


def _inside(parent, card_ids):
    return False  # parents of groups are not cards themselves in the first pass


def slots(card):
    """Leaf slots of a card in reading order: [('text'|'image'|'button', path)]."""
    res = []

    def walk(n, path):
        t = n.get('type')
        if 'asset' in n:
            if n['asset'].get('format') != 'svg' or n.get('width', 0) > 80:
                res.append(('image', tuple(path), n))
            return
        if t == 'TEXT':
            res.append(('text', tuple(path), n))
            return
        ch = kids(n)
        if not ch:
            if any(p.get('type') == 'IMAGE' for p in (n.get('fills') or []) if p.get('visible', True)):
                res.append(('image', tuple(path), n))
            return
        for i, c in enumerate(ch):
            walk(c, path + [i])
    walk(card, [])
    return res


def get_at(card, path):
    n = card
    for i in path:
        n = kids(n)[i]
    return n


def build_schema(section_title, cards, images_by_hash=None, plural=None):
    """-> (post_type dict, acf field group dict, [post items], slot_map {path: field_name})"""
    sl = slots(cards[0])
    base = slug(plural or section_title or 'cards', 20)
    pt_slug = base if len(base) > 3 else base + '_item'
    pt_slug = pt_slug[:20]
    fields, slot_map, used = [], {}, Counter()
    for kind, path, node in sl:
        label = slug(node.get('name') or kind, 18)
        if kind == 'text':
            txt = node.get('characters') or ''
            ftype = 'textarea' if len(txt) > 90 else 'text'
            nm = label if label not in ('text', 'frame') else 'text'
        elif kind == 'image':
            ftype, nm = 'image', 'image'
        else:
            ftype, nm = 'text', label
        used[nm] += 1
        name = nm if used[nm] == 1 else f'{nm}_{used[nm]}'
        key = 'field_' + hashlib.md5(f'{pt_slug}:{name}'.encode()).hexdigest()[:13]
        f = {'key': key, 'label': name.replace('_', ' ').title(), 'name': name, 'type': ftype}
        if ftype == 'image':
            f['return_format'] = 'array'
            f['preview_size'] = 'medium'
        fields.append(f)
        slot_map[path] = (name, key, ftype)
    group = {
        'key': 'group_' + hashlib.md5(pt_slug.encode()).hexdigest()[:13],
        'title': f'{pt_slug.replace("_", " ").title()} fields',
        'fields': fields,
        'location': [[{'param': 'post_type', 'operator': '==', 'value': pt_slug}]],
        'position': 'normal', 'style': 'default', 'active': True,
    }
    pt = {'slug': pt_slug, 'label': pt_slug.replace('_', ' ').title(), 'has_archive': True, 'supports': ['title', 'editor', 'thumbnail'],
          'icon': 'dashicons-screenoptions'}
    return pt, group, slot_map


def post_items(cards, slot_map):
    items = []
    for c in cards:
        title, meta, media = None, {}, {}
        for path, (name, key, ftype) in slot_map.items():
            node = get_at(c, path)
            if ftype == 'image':
                media[name] = node
                continue
            txt = (node.get('characters') or '').strip()
            meta[name] = txt
            if title is None and txt:
                title = txt
        items.append({'title': title or 'Item', 'meta': meta, 'media': media, 'status': 'publish'})
    return items


# ---------------------------------------------------------------- Elementor Pro dynamic tags (documented format; untested here)

def dyn_tag(name, settings, tag_id=None):
    tid = tag_id or hashlib.md5(json.dumps([name, settings], sort_keys=True).encode()).hexdigest()[:7]
    enc = urllib.parse.quote(json.dumps(settings, separators=(',', ':')))
    return f'[elementor-tag id="{tid}" name="{name}" settings="{enc}"]'


def acf_tag(ftype, key, name):
    tname = {'image': 'acf-image', 'textarea': 'acf-textarea', 'url': 'acf-url'}.get(ftype, 'acf-text')
    return dyn_tag(tname, {'key': f'{key}:{name}'})


def apply_dynamic(card_element, slot_to_element_id, slot_map_by_elid):
    """Mark widgets of the loop-item template as dynamic (ACF fields). Mutates card_element."""
    def walk(el):
        if el.get('elType') == 'widget' and el['id'] in slot_map_by_elid:
            name, key, ftype = slot_map_by_elid[el['id']]
            dyn = el['settings'].setdefault('__dynamic__', {})
            wt = el['widgetType']
            if wt == 'heading':
                dyn['title'] = acf_tag(ftype, key, name)
            elif wt == 'text-editor':
                dyn['editor'] = acf_tag(ftype, key, name)
            elif wt == 'image':
                dyn['image'] = acf_tag('image', key, name)
            elif wt == 'button':
                dyn['text'] = acf_tag('text', key, name)
        for c in el.get('elements', []):
            walk(c)
    walk(card_element)
    return card_element


def loop_grid_widget(template_id, post_type, columns=3, per_page=9):
    return {'id': hashlib.md5(f'lg{template_id}'.encode()).hexdigest()[:7], 'elType': 'widget', 'widgetType': 'loop-grid', 'elements': [],
            'settings': {'template_id': str(template_id), 'columns': columns, 'posts_per_page': per_page,
                         'post_query_post_type': post_type, 'post_query_posts_per_page': per_page}}
