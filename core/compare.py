"""Side-by-side accuracy check (skill 96: 'compare each page with the original HTML at every breakpoint').

reference = the exact HTML build of the Figma frame (or a Figma render PNG), candidate = the live WordPress/Elementor page.
Outputs match %, diff image and the worst horizontal bands (so a fixer knows WHERE it differs).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import verify  # noqa: E402


def shot_page(browser, url_or_file, width, height, scale, dest, wait_ms=500, headers=None):
    from PIL import Image  # noqa: F401
    ctx = browser.new_context(viewport={'width': width, 'height': 900}, device_scale_factor=scale, extra_http_headers=headers or None)
    page = ctx.new_page()
    url = url_or_file if str(url_or_file).startswith('http') else Path(url_or_file).resolve().as_uri()
    page.goto(url, wait_until='load', timeout=180000)
    try:
        page.wait_for_load_state('networkidle', timeout=20000)
    except Exception:
        pass
    total = page.evaluate('document.documentElement.scrollHeight')
    for y in range(0, int(total), 800):      # trigger lazy images / motion
        page.evaluate(f'window.scrollTo(0,{y})')
        page.wait_for_timeout(40)
    page.evaluate('window.scrollTo(0,0)')
    page.wait_for_timeout(wait_ms)
    verify.prepare_page(page)
    h = min(int(page.evaluate('document.documentElement.scrollHeight')), int(height) if height else 10 ** 6)
    verify.capture(page, width, h, scale, dest)
    ctx.close()
    return h


def section_report(url, width, expected, headers=None):
    """Layout fidelity per top-level Elementor section: where does each section start and how tall is it vs the design?
    expected: [(title, y, h)] sorted by y (from the Figma frame). Returns rows with dy / dh (px)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = verify.launch(p, lambda s: None)
        ctx = browser.new_context(viewport={'width': width, 'height': 900}, extra_http_headers=headers or None)
        page = ctx.new_page()
        page.goto(url, wait_until='load', timeout=180000)
        try:
            page.wait_for_load_state('networkidle', timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(500)
        got = page.evaluate("""() => [...document.querySelectorAll('.elementor-element.e-parent')].map(e => {
            const r = e.getBoundingClientRect(); return {y: Math.round(r.top + scrollY), h: Math.round(r.height), w: Math.round(r.width)}; })""")
        browser.close()
    rows = []
    for i, (title, y, h) in enumerate(expected):
        g = got[i] if i < len(got) else None
        rows.append({'section': title, 'design_y': round(y), 'design_h': round(h), 'page_y': g and g['y'], 'page_h': g and g['h'],
                     'dy': g and g['y'] - round(y), 'dh': g and g['h'] - round(h)})
    return rows, len(got)


def compare(reference, candidate, out_dir, width, tolerance=24, scale=1, bands=24, log=print, height=None):
    """reference/candidate: html file path or http URL. Returns result dict (also written to out_dir/compare.json)."""
    from PIL import Image
    from playwright.sync_api import sync_playwright
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ref_png, cand_png, diff_png = out_dir / 'reference.png', out_dir / 'candidate.png', out_dir / 'diff.png'
    with sync_playwright() as p:
        browser = verify.launch(p, lambda s: None)
        rh = shot_page(browser, reference, width, height, scale, ref_png)
        ch = shot_page(browser, candidate, width, height or rh, scale, cand_png)
        browser.close()
    ref = Image.open(ref_png).convert('RGB')
    cand = Image.open(cand_png).convert('RGB')
    canvas = Image.new('RGB', ref.size, (255, 255, 255))
    canvas.paste(cand.crop((0, 0, min(cand.width, ref.width), min(cand.height, ref.height))), (0, 0))
    refrgb, mask, match = verify.diff_mask(ref, canvas, tolerance)
    # tolerant metric: blur both first, so image re-compression / anti-aliasing noise does not count, only real shape/text/colour differences
    from PIL import ImageFilter
    _, mask_t, match_t = verify.diff_mask(ref.filter(ImageFilter.GaussianBlur(2.5)), canvas.filter(ImageFilter.GaussianBlur(2.5)), 36)
    verify.write_diff(refrgb, mask_t, diff_png)
    bh = max(1, mask.height // bands)
    rows = []
    for i in range(bands):
        y0, y1 = i * bh, mask.height if i == bands - 1 else (i + 1) * bh
        bad = mask_t.crop((0, y0, mask_t.width, y1)).histogram()[255]
        rows.append({'y': round(y0 / scale), 'height': round((y1 - y0) / scale), 'mismatch': round(bad / ((y1 - y0) * mask.width) * 100, 2)})
    res = {'match': match_t, 'strict_match': match, 'reference_height': round(ref.height / scale), 'candidate_height': round(cand.height / scale),
           'bands': rows, 'worst': sorted(rows, key=lambda r: -r['mismatch'])[:6],
           'files': {'reference': str(ref_png), 'candidate': str(cand_png), 'diff': str(diff_png)}}
    (out_dir / 'compare.json').write_text(json.dumps(res, indent=1), 'utf-8')
    log(f'match {match_t}% (tolerant)  /  {match}% (strict pixel)  (reference {res["reference_height"]}px, page {res["candidate_height"]}px)')
    return res
