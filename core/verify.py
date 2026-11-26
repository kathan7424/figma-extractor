#!/usr/bin/env python3
"""
verify.py - pixel-compare generated HTML with Figma's own PNG renders, and (optionally) AUTO-FIX.

Free & local. Uses the browser already on Windows (Edge/Chrome) through Playwright - no download needed.
    pip install playwright pillow

Usage:  python verify.py <output-folder> [--autofix] [--target 99] [--tolerance 24]

--autofix: nodes whose pixels differ from Figma's render are replaced by a crop of Figma's own render
(deepest offending node first, limited to 35% of the frame area) and the frame is re-checked,
up to --rounds times. Result: you reach the target match even for effects CSS cannot reproduce.
Writes verify/*-html.png, verify/*-diff.png, verify-report.html, overrides.json and manifest.json.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import sys
import time
from pathlib import Path

RASTER_BUDGET = 0.35  # max share of a frame's area that auto-fix may turn into images
TILE = 3000  # css px per screenshot tile (tall frames)


def launch(p, log):
    errors = []
    for kwargs in ({'channel': 'msedge'}, {'channel': 'chrome'}, {}):
        try:
            b = p.chromium.launch(**kwargs)
            log(f'  browser: {kwargs.get("channel", "playwright-chromium")}')
            return b
        except Exception as e:
            errors.append(str(e).splitlines()[0])
    raise RuntimeError('Browser nathi malyu (Edge/Chrome joiye): ' + ' | '.join(errors))


def prepare_page(page):
    """Fonts + lazy images loaded, so the screenshot shows everything."""
    page.evaluate('document.fonts.ready.then(() => true)')
    page.evaluate("""() => Promise.all([...document.images].map(i => { i.loading = 'eager';
        return i.complete ? 0 : new Promise(r => { i.onload = i.onerror = r; }); }))""")


def capture(page, w: int, h: int, scale: float, dest: Path, element=None):
    """Screenshot the top w x h css px (element or page), in tiles for very tall pages."""
    import io
    from PIL import Image
    if element and h * scale <= 12000:
        page.locator(element).first.screenshot(path=str(dest), animations='disabled')
        return
    img = Image.new('RGB', (round(w * scale), round(h * scale)), (255, 255, 255))
    y = 0
    while y < h:
        ch = min(TILE, h - y)
        png = page.screenshot(clip={'x': 0, 'y': y, 'width': w, 'height': ch}, full_page=True, animations='disabled')
        img.paste(Image.open(io.BytesIO(png)).convert('RGB'), (0, round(y * scale)))
        y += ch
    img.save(dest)


def shoot(page, html_file: Path, w: int, h: int, scale: float, dest: Path):
    """Screenshot the frame root element at device scale `scale` (tiles for very tall frames)."""
    page.set_viewport_size({'width': w, 'height': min(max(h, 100), 2000)})
    page.goto(html_file.resolve().as_uri(), wait_until='networkidle', timeout=180000)
    prepare_page(page)
    capture(page, w, h, scale, dest, '.fek-fit > *, body > div')


def diff_mask(ref_png, got_png, tolerance=24):
    """Returns (ref RGB image, mask L image (255 = differs), match %). Accepts paths or PIL images."""
    from PIL import Image, ImageChops
    ref = ref_png if isinstance(ref_png, Image.Image) else Image.open(ref_png)
    ref = ref.convert('RGBA')
    ref = Image.alpha_composite(Image.new('RGBA', ref.size, (255, 255, 255, 255)), ref).convert('RGB')
    got = (got_png if isinstance(got_png, Image.Image) else Image.open(got_png)).convert('RGB')
    if got.size != ref.size:
        got = got.resize(ref.size)
    r, g, b = ImageChops.difference(ref, got).split()
    mx = ImageChops.lighter(ImageChops.lighter(r, g), b)
    mask = mx.point(lambda v: 255 if v > tolerance else 0)
    total = ref.size[0] * ref.size[1] or 1
    return ref, mask, round(100 - mask.histogram()[255] / total * 100, 2)


def write_diff(ref, mask, dest: Path):
    from PIL import Image
    faded = Image.blend(ref, Image.new('RGB', ref.size, (255, 255, 255)), 0.7)
    Image.composite(Image.new('RGB', ref.size, (230, 30, 30)), faded, mask).save(dest)


def node_box(n):
    bb = n.get('absoluteBoundingBox')
    if bb:
        return bb['x'], bb['y'], bb['width'], bb['height']
    t = n.get('absoluteTransform')
    if t and n.get('width') is not None:
        # axis-aligned box of the (possibly rotated) node
        pts = [(t[0][0] * x + t[0][1] * y + t[0][2], t[1][0] * x + t[1][1] * y + t[1][2])
               for x, y in ((0, 0), (n['width'], 0), (0, n['height']), (n['width'], n['height']))]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
    return None


def find_offenders(root, mask, scale, done_ids, min_px, min_ratio):
    """Deepest nodes whose bounding box contains many differing pixels. Returns [(bad, node, box)]."""
    rb = node_box(root)
    if not rb:
        return []
    rx, ry = rb[0], rb[1]
    W, H = mask.size

    def box_of(n):
        b = node_box(n)
        if not b or b[2] < 1 or b[3] < 1:
            return None
        x0, y0 = max(0, math.floor((b[0] - rx) * scale)), max(0, math.floor((b[1] - ry) * scale))
        x1, y1 = min(W, math.ceil((b[0] - rx + b[2]) * scale)), min(H, math.ceil((b[1] - ry + b[3]) * scale))
        return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None

    def walk(n, is_root):
        kids = [] if n.get('asset') else [c for c in n.get('children', []) if c.get('id') not in done_ids]
        found, kid_bad = [], 0
        for c in kids:
            found += walk(c, False)
            cb = box_of(c)
            if cb:
                kid_bad += mask.crop(cb).histogram()[255]
        if is_root:
            return found
        bx = box_of(n)
        if not bx:
            return found
        bad = mask.crop(bx).histogram()[255]
        own = max(0, bad - kid_bad)  # mismatch that the children do not explain (background, border, shadow...)
        area = (bx[2] - bx[0]) * (bx[3] - bx[1])
        if own >= min_px * scale * scale and own / area >= min_ratio:
            return found + [(bad, n, bx)]
        return found

    # smallest area first: precise fixes before big containers
    return sorted(walk(root, True), key=lambda t: (t[2][2] - t[2][0]) * (t[2][3] - t[2][1]))


def verify(out, tolerance=24, log=print, autofix=False, target=99.0, rounds=3, min_px=40, min_ratio=0.02):
    try:
        from playwright.sync_api import sync_playwright
        import PIL  # noqa: F401
    except ImportError:
        log('Playwright/Pillow nathi. Chalavo: install.bat  (ke: pip install playwright pillow)')
        return None
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import figma2html

    out = Path(out)
    manifest_path = out / 'manifest.json'
    m = json.loads(manifest_path.read_text('utf-8'))
    vdir = out / 'verify'
    vdir.mkdir(exist_ok=True)
    frames = [f for f in m.get('frames', []) if f.get('render') and f.get('html')]
    if not frames:
        log('Koi frame ma Figma render nathi (plugin ma "Frame renders" tick karo).')
        return []
    ov_path = out / 'overrides.json'
    ov_all = json.loads(ov_path.read_text('utf-8')) if ov_path.exists() else {}
    conv = m.get('converter', {})
    t0 = time.time()
    state = {f['id']: {'fr': f, 'node': None, 'spent': 0} for f in frames}
    pending = list(frames)
    results = {}

    with sync_playwright() as p:
        browser = launch(p, log)
        pages = {}
        for rnd in range(1, (rounds if autofix else 1) + 1):
            changed = []
            for i, fr in enumerate(pending, 1):
                s = float(fr.get('renderScale') or m.get('renderScale') or 1)
                if s not in pages:
                    pages[s] = browser.new_page(device_scale_factor=s)
                w, h = max(1, math.ceil(fr['width'])), max(1, math.ceil(fr['height']))
                tag = f'{Path(fr["html"]).parent.name}--{Path(fr["html"]).stem}'
                shot, diff = vdir / f'{tag}-html.png', vdir / f'{tag}-diff.png'
                shoot(pages[s], out / fr['html'], w, h, s, shot)
                ref, mask, match = diff_mask(out / fr['render'], shot, tolerance)
                write_diff(ref, mask, diff)
                st = state[fr['id']]
                fixes = len(ov_all.get(fr['id'], {}))
                fr['verify'] = {'match': match, 'shot': shot.relative_to(out).as_posix(),
                                'diff': diff.relative_to(out).as_posix(), 'rounds': rnd, 'fixes': fixes}
                results[fr['id']] = fr
                log(f'  round {rnd} [{i}/{len(pending)}] {fr["name"]}: {match}%' + (f'  ({fixes} node(s) auto-fixed)' if fixes else ''))
                if not autofix or match >= target or rnd == rounds:
                    continue
                if st['node'] is None:
                    st['node'] = json.loads((out / fr['json']).read_text('utf-8'))
                done = set(ov_all.get(fr['id'], {}))
                offenders = find_offenders(st['node'], mask, s, done, min_px, min_ratio)
                budget = w * h * RASTER_BUDGET
                added = 0
                for bad, node, bx in offenders:
                    area = (bx[2] - bx[0]) * (bx[3] - bx[1]) / (s * s)
                    if st['spent'] + area > budget:
                        continue
                    x0, y0, x1, y1 = bx
                    dest = out / 'assets' / 'raster' / f'fix-{slug_id(fr["id"])}-{slug_id(node["id"])}.png'
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    ref.crop(bx).save(dest)
                    nb = node_box(st['node'])
                    rx, ry = nb[0], nb[1]
                    ov_all.setdefault(fr['id'], {})[node['id']] = {
                        'path': dest.relative_to(out).as_posix(), 'format': 'png', 'scale': s, 'fix': True,
                        'renderBounds': {'x': rx + x0 / s, 'y': ry + y0 / s, 'width': (x1 - x0) / s, 'height': (y1 - y0) / s}}
                    st['spent'] += area
                    added += 1
                if added:
                    changed.append(fr)
                    log(f'      -> {added} node(s) replaced by Figma render crop')
            if not changed:
                break
            ov_path.write_text(json.dumps(ov_all), 'utf-8')
            manifest_path.write_text(json.dumps(m, indent=2), 'utf-8')
            figma2html.convert(out, out, mode=conv.get('mode', 'exact'), asset_base_url=conv.get('assetBaseUrl'),
                               elementor=conv.get('elementor', False), fit=conv.get('fit', True), log=lambda s: None)
            m = json.loads(manifest_path.read_text('utf-8'))
            by_id = {f['id']: f for f in m['frames']}
            for fid, st in state.items():
                st['fr'] = by_id[fid]
            # keep the verify info we just measured
            for fid, r in results.items():
                by_id[fid]['verify'] = r['verify']
            results = {fid: by_id[fid] for fid in results}
            frames = [by_id[f['id']] for f in frames]
            pending = [by_id[f['id']] for f in changed]
        browser.close()

    m['verified'] = True
    manifest_path.write_text(json.dumps(m, indent=2), 'utf-8')
    rows = [f for f in m['frames'] if f.get('verify')]
    (out / 'verify-report.html').write_text(report(m, rows, tolerance, target), 'utf-8')
    (out / 'index.html').write_text(figma2html.gallery_html(m), 'utf-8')
    avg = round(sum(r['verify']['match'] for r in rows) / len(rows), 2)
    log(f'Average pixel match: {avg}%  ({time.time() - t0:.1f}s)  ->  {out / "verify-report.html"}')
    return rows


def verify_url(out, frame, url, tolerance=24, log=print, headers=None, bands=24):
    """Compare a LIVE page (e.g. the WordPress/Elementor page) with the Figma render of one frame.
    Writes verify-url/<frame>-page.png, -diff.png and -result.json (match % + worst horizontal bands).
    `frame` = frame id or part of its name. `headers` = dict of extra HTTP headers (e.g. auth)."""
    from playwright.sync_api import sync_playwright
    from PIL import Image
    out = Path(out)
    m = json.loads((out / 'manifest.json').read_text('utf-8'))
    q = str(frame).lower()
    cands = [f for f in m['frames'] if f.get('render') and (f['id'] == frame or q in f['name'].lower())]
    if not cands:
        raise ValueError(f'Frame "{frame}" (with Figma render) nathi malyu')
    fr = cands[0]
    s = float(fr.get('renderScale') or m.get('renderScale') or 1)
    w, h = max(1, math.ceil(fr['width'])), max(1, math.ceil(fr['height']))
    vdir = out / 'verify-url'
    vdir.mkdir(exist_ok=True)
    tag = ''.join(c if c.isalnum() else '-' for c in fr['name'])[:40].strip('-') or 'frame'
    shot, diff = vdir / f'{tag}-page.png', vdir / f'{tag}-diff.png'
    with sync_playwright() as p:
        browser = launch(p, log)
        ctx = browser.new_context(viewport={'width': w, 'height': 900}, device_scale_factor=s,
                                  extra_http_headers=headers or None)
        page = ctx.new_page()
        page.goto(url, wait_until='load', timeout=120000)
        try:
            page.wait_for_load_state('networkidle', timeout=20000)
        except Exception:
            pass
        total = page.evaluate('document.documentElement.scrollHeight')
        for y in range(0, int(total), 700):  # trigger lazy-load / scroll animations
            page.evaluate(f'window.scrollTo(0, {y})')
            page.wait_for_timeout(60)
        page.evaluate('window.scrollTo(0, 0)')
        page.wait_for_timeout(300)
        prepare_page(page)
        capture(page, w, h, s, shot)
        browser.close()
    ref = Image.open(out / fr['render'])
    got = Image.open(shot).convert('RGB')
    canvas = Image.new('RGB', ref.size, (255, 255, 255))
    canvas.paste(got.crop((0, 0, min(got.width, ref.width), min(got.height, ref.height))), (0, 0))
    refrgb, mask, match = diff_mask(ref, canvas, tolerance)
    write_diff(refrgb, mask, diff)
    bh = max(1, mask.height // bands)
    rows = []
    for i in range(bands):
        y0, y1 = i * bh, mask.height if i == bands - 1 else (i + 1) * bh
        bad = mask.crop((0, y0, mask.width, y1)).histogram()[255]
        rows.append({'y': round(y0 / s), 'height': round((y1 - y0) / s), 'mismatch': round(bad / ((y1 - y0) * mask.width) * 100, 2)})
    result = {'frame': fr['name'], 'url': url, 'match': match, 'size': [w, h], 'scale': s,
              'pageHeight': round(got.height / s), 'worstBands': sorted(rows, key=lambda r: -r['mismatch'])[:8],
              'files': {'page': shot.relative_to(out).as_posix(), 'diff': diff.relative_to(out).as_posix(), 'figma': fr['render']}}
    (vdir / f'{tag}-result.json').write_text(json.dumps(result, indent=2), 'utf-8')
    log(f'{fr["name"]}: {match}% match  (page height {result["pageHeight"]}px vs frame {h}px)')
    return result


def slug_id(s):
    return ''.join(c if c.isalnum() else '-' for c in str(s)).strip('-')


def report(m, rows, tolerance, target):
    e = html.escape
    body = []
    for r in sorted(rows, key=lambda r: r['verify']['match']):
        v = r['verify']
        fix = f' · {v["fixes"]} node(s) from Figma render' if v.get('fixes') else ''
        body.append(f'''<section><h2>{e(r["name"])} <span class="{"ok" if v["match"] >= target else "warn"}">{v["match"]}%</span><small>{fix}</small></h2>
<div class="row"><figure><img src="{e(r["render"])}"><figcaption>Figma</figcaption></figure>
<figure><img src="{e(v["shot"])}"><figcaption>HTML</figcaption></figure>
<figure><img src="{e(v["diff"])}"><figcaption>Diff (red = different)</figcaption></figure></div></section>''')
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Verify report</title><style>
:root {{ --bg:#f6f6f4; --card:#fff; --text:#1c1c1a; --muted:#6b6b66; --ok:#1d7a3a; --warn:#b5530b; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#161615; --card:#21211f; --text:#ececea; --muted:#9a9a95; --ok:#5fd07f; --warn:#f0a35e; }} }}
body {{ margin:0; font:14px/1.5 system-ui, sans-serif; background:var(--bg); color:var(--text); }}
main {{ max-width:1400px; margin:0 auto; padding:24px 16px; }}
section {{ background:var(--card); border-radius:10px; padding:12px 16px; margin:0 0 16px; }}
h2 {{ font-size:15px; }} h2 small {{ color:var(--muted); font-weight:400; }} .ok {{ color:var(--ok); }} .warn {{ color:var(--warn); }}
.row {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:12px; }}
figure {{ margin:0; }} img {{ width:100%; border:1px solid #8884; }} figcaption {{ color:var(--muted); font-size:12px; }}
</style></head><body><main><h1>Visual verify: {e(m.get("fileName", ""))}</h1>
<p>Tolerance {tolerance}/255 per channel, target {target}%. Lowest match first.</p>{"".join(body)}</main></body></html>'''


def main(argv=None):
    ap = argparse.ArgumentParser(description='Pixel-compare generated HTML against Figma renders (+auto-fix)')
    ap.add_argument('out', help='output folder made by figma2html.py')
    ap.add_argument('--tolerance', type=int, default=24)
    ap.add_argument('--autofix', action='store_true', help='replace mismatching nodes by crops of the Figma render')
    ap.add_argument('--target', type=float, default=99.0)
    ap.add_argument('--rounds', type=int, default=3)
    ap.add_argument('--url', help='compare a live page (WordPress/Elementor) with a frame instead')
    ap.add_argument('--frame', help='frame id or name part (with --url)')
    a = ap.parse_args(argv)
    if a.url:
        import os
        hdr = json.loads(os.environ['FEK_URL_HEADERS']) if os.environ.get('FEK_URL_HEADERS') else None
        verify_url(a.out, a.frame or '', a.url, a.tolerance, headers=hdr)
        return
    verify(a.out, a.tolerance, autofix=a.autofix, target=a.target, rounds=a.rounds)


if __name__ == '__main__':
    sys.exit(main())
