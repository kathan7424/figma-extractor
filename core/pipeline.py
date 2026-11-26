"""High-level pipeline: export folder -> native Elementor (kit + page) -> optional push to WordPress."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import dynamic  # noqa: E402
import elementor_native as en  # noqa: E402
import media as mediamod  # noqa: E402
import raster as rastermod  # noqa: E402


def load_frame(export_dir, frame_query):
    export_dir = Path(export_dir)
    m = json.loads((export_dir / 'manifest.json').read_text('utf-8'))
    q = str(frame_query).lower()
    fr = next((f for f in m['frames'] if f['id'] == frame_query or q in f['name'].lower()), None)
    if not fr:
        raise ValueError(f'Frame "{frame_query}" nathi malyu')
    node = json.loads((export_dir / fr['json']).read_text('utf-8'))
    return m, fr, node


def plan_loops(node):
    """Elementor Pro / ACF: repeated cards -> CPT + ACF fields + loop item. Returns (loop map for the builder, schema list)."""
    loops, schema, used = {}, [], set()
    for parent, cards in dynamic.find_repeat_groups(node)[:4]:
        title = next((c.get('characters') for c in dynamic.kids(parent) if c.get('type') == 'TEXT' and c.get('characters')), 'cards')
        pt, group, slot_map = dynamic.build_schema(title, cards)
        base, k = pt['slug'], 2
        while pt['slug'] in used:
            pt['slug'] = base[:17] + '_' + str(k)
            k += 1
        used.add(pt['slug'])
        group['location'] = [[{'param': 'post_type', 'operator': '==', 'value': pt['slug']}]]
        boxes = [en.bbox(c) for c in cards]
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        bbox_all = (x0, y0, max(b[0] + b[2] for b in boxes) - x0, max(b[1] + b[3] for b in boxes) - y0)
        cols = max(1, sum(1 for b in boxes if abs(b[1] - boxes[0][1]) <= 6))
        for i, c in enumerate(cards):
            loops[id(c)] = {'pt': pt['slug'], 'index': i, 'slots': dynamic.slots(cards[0]), 'slot_map': slot_map, 'bbox': bbox_all,
                            'columns': cols, 'per_page': len(cards)}
        schema.append((pt, group, slot_map, cards))
    return loops, schema


def split_header_footer(builder, elements, root):
    """Elementor Pro Theme Builder: header/footer sections become templates (skill 105-108)."""
    secs = builder.section_nodes
    rx, ry, rw, rh = en.bbox(root)
    hdr, ftr = [], []
    for i, s in enumerate(secs):
        x, y, w, h = en.bbox(s)
        nm = s.get('name', '')
        if (y - ry) <= 200 and h <= 240 and not re.search(r'slider|hero|banner|cover', nm, re.I) and all(j in hdr for j in range(i)):
            hdr.append(i)
        elif re.search(r'footer', nm, re.I) or (i == len(secs) - 1 and y + h >= ry + rh - 5 and h >= 300):
            ftr.append(i)
    keep = [e for i, e in enumerate(elements) if i not in hdr and i not in ftr]

    def clean(els):
        return [{**e, 'settings': {k: v for k, v in e['settings'].items() if k not in ('margin', 'z_index')}} for e in els]
    return keep, clean([elements[i] for i in hdr]), clean([elements[i] for i in ftr])


def collect_masks(node):
    out = []

    def walk(n):
        if n.get('maskAsset'):
            out.append(n)
        for c in n.get('children', []) or []:
            walk(c)
    walk(node)
    return out


def build(export_dir, frame_query, link_map=None, pro=False, mode='exact', rasterize=True, log=print):
    """Returns dict(kit, title, elements, media, ...). pro=True also plans header/footer templates + Loop Grids (ACF dynamic)."""
    m, fr, node = load_frame(export_dir, frame_query)
    ds = en.DesignSystem()
    loops, schema = plan_loops(node) if pro else ({}, [])
    raster = {}
    masks = collect_masks(node) if rasterize else []
    html = Path(export_dir) / fr.get('html', '')
    if masks and html.is_file():
        log(f'  rendering {len(masks)} masked group(s) from the exact HTML ...')
        try:
            raster = rastermod.render(html, masks, Path(export_dir) / 'assets' / 'raster', log=log)
        except Exception as e:   # browser missing: keep going without masks
            log(f'  ! mask render skipped: {e}')
    b = en.Builder(export_dir, m, ds, link_map=link_map, pro=pro, log=log)
    b.exact = mode == 'exact'
    b.raster = raster
    b.loops = loops
    b.build_page(node)   # pass 1 collects colours/typography (globals must exist before widgets can link them)
    kit = ds.finish(container_width=b.container_w)
    b2 = en.Builder(export_dir, m, ds, link_map=link_map, pro=pro, log=log)
    b2.exact = mode == 'exact'
    b2.raster = raster
    b2.loops = loops
    b2.container_w = b.container_w
    elements = b2.build_page(node)
    header = footer = []
    if pro:
        elements, header, footer = split_header_footer(b2, elements, node)
    page_settings = {}
    solid = [f for f in en.visible_paints(node.get('fills')) if f.get('type') == 'SOLID']
    if solid:   # the page background is the root frame's fill (skill 127: no design drift)
        page_settings = {'background_background': 'classic', 'background_color': en.hexa(solid[-1]['color'], solid[-1].get('opacity', 1))}
    return {'page_settings': page_settings, 'kit': kit, 'title': fr['name'], 'elements': elements, 'header': header, 'footer': footer, 'media': b2.media,
            'links_todo': sorted(set(b2.links_todo)), 'stats': dict(b2.stats), 'warnings': dict(b2.warn),
            'fonts_used': [{'family': f, 'style': s} for f, s in sorted(b2.fonts_used)], 'builder': b2, 'frame': fr,
            'container_width': b.container_w, 'schema': schema, 'loop_templates': b2.loop_templates}


def push(result, export_dir, wp, work_dir, slug=None, status='publish', front_page=False, template='elementor_canvas', fonts_dir=None, log=print):
    """Upload media (WebP), set the Kit, create the page. Returns the page info."""
    import hashlib
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    files = mediamod.prepare(export_dir, result['media'], work_dir / 'media', log=log)
    cache_file = work_dir / 'media-cache.json'
    cache = json.loads(cache_file.read_text('utf-8')) if cache_file.exists() else {}
    uploaded, by_file, new = {}, {}, 0
    for key, f in files.items():
        if str(f) in by_file:
            uploaded[key] = by_file[str(f)]
            continue
        ck = wp.url + '|' + hashlib.sha1(Path(f).read_bytes()).hexdigest()
        meta = result['media'][key]
        if ck in cache:  # skill 140: every image/icon is uploaded to the Media Library only once
            up = cache[ck]
        else:
            up = wp.upload_media(f, alt=meta.get('alt', ''), title=meta.get('name', ''))
            cache[ck] = up
            new += 1
            cache_file.write_text(json.dumps(cache), 'utf-8')
        by_file[str(f)] = up
        uploaded[key] = up
    log(f'  media: {len(by_file)} files ({new} newly uploaded)')
    mediamod.resolve(result['elements'], uploaded)
    for key in ('header', 'footer'):
        mediamod.resolve(result.get(key) or [], uploaded)
    for el in result.get('loop_templates', {}).values():
        mediamod.resolve([el], uploaded)
    if fonts_dir:
        used = {(f['family'], f.get('style', 'Regular')) for f in result.get('fonts_used', [])}
        n = wp.upload_fonts(fonts_dir, used, cache_file=work_dir / 'fonts-cache.json')
        log(f'  fonts: {n} files sent')
    if result.get('kit'):
        wp.kit(result['kit'])
    # ---- Elementor Pro + ACF PRO parts (Pro JSON is generated from Elementor's documented format; NOT run on a Pro site yet)
    if result.get('schema'):
        wp.schema([s[0] for s in result['schema']], [s[1] for s in result['schema']])
        for pt, group, slot_map, cards in result['schema']:
            items = dynamic.post_items(cards, slot_map)
            posts = []
            for it in items:
                meta = dict(it['meta'])
                for name, node in it['media'].items():
                    ref = node['asset']['path'] if 'asset' in node else next(
                        (result['builder'].images[p['imageHash']]['path'] for p in (node.get('fills') or [])
                         if p.get('type') == 'IMAGE' and p.get('imageHash') in result['builder'].images), None)
                    if ref:
                        f = mediamod.prepare(export_dir, {'k': {'ref': ref, 'name': name}}, work_dir / 'media', log=log).get('k')
                        if f:
                            meta[name] = wp.upload_media(f, alt=it['title'])['id']
                posts.append({'title': it['title'], 'meta': meta})
            wp.posts(pt['slug'], posts)
            tpl = result['loop_templates'].get(pt['slug'])
            if tpl:
                tid = wp.template('Loop item - ' + pt['slug'], 'loop-item', [tpl])['id']
                _replace_template_id(result['elements'], pt['slug'], tid)
        log(f'  dynamic: {len(result["schema"])} post type(s) with ACF fields')
    if result.get('header'):
        wp.template('Header', 'header', result['header'], ['include/general'])
    if result.get('footer'):
        wp.template('Footer', 'footer', result['footer'], ['include/general'])
    return wp.page(result['title'], result['elements'], status=status, slug=slug or '', template=template, front_page=front_page, page_settings=result.get('page_settings') or None)


def _replace_template_id(elements, pt, tid):
    for e in elements:
        if e.get('widgetType') == 'loop-grid' and str(e['settings'].get('template_id')) == 'FTE_TEMPLATE:' + pt:
            e['settings']['template_id'] = str(tid)
        _replace_template_id(e.get('elements', []), pt, tid)
