"""
raster.py - render selected design nodes to transparent PNGs using the EXACT HTML build (so masks, blends and effects are pixel-true).

Used for things Elementor cannot do natively (Figma masks). Each result becomes a normal Elementor image widget.
Needs Playwright + Edge/Chrome (same as the accuracy check).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import verify  # noqa: E402


def classname(node):
    """Same class name figma2html gives a node."""
    idpart = re.sub(r'[^a-zA-Z0-9]+', '-', node.get('id', 'x')).strip('-')
    s = re.sub(r'[^a-zA-Z0-9]+', '-', str(node.get('name') or '')).strip('-').lower()[:24].strip('-') or 'node'
    name = f'{s}-{idpart}'
    return name if name[0].isalpha() else 'n' + name


def render(html_file, nodes, out_dir, scale=2, log=print):
    """nodes: [figma node dicts]. Returns {node id: PNG path}. Everything else on the page is hidden, background transparent."""
    from playwright.sync_api import sync_playwright
    html_file, out_dir = Path(html_file), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    if not nodes:
        return out
    with sync_playwright() as p:
        browser = verify.launch(p, lambda s: None)
        page = browser.new_page(viewport={'width': 1920, 'height': 1000}, device_scale_factor=scale)
        page.goto(html_file.resolve().as_uri(), wait_until='load', timeout=180000)
        try:
            page.wait_for_load_state('networkidle', timeout=20000)
        except Exception:
            pass
        verify.prepare_page(page)
        page.add_style_tag(content='html,body,.fek-fit{background:transparent!important}')
        for n in nodes:
            cls = classname(n)
            sel = '.' + cls
            loc = page.locator(sel).first
            if loc.count() == 0:
                log(f'  ! raster: element {cls} not found')
                continue
            loc.scroll_into_view_if_needed()
            page.evaluate("""(sel) => {
                let st = document.getElementById('fte-iso'); if (st) st.remove();
                st = document.createElement('style'); st.id = 'fte-iso';
                st.textContent = 'body *{visibility:hidden!important} ' + sel + ', ' + sel + ' *{visibility:visible!important}';
                document.head.appendChild(st); }""", sel)
            dest = out_dir / f'raster-{re.sub(r"[^a-zA-Z0-9]+", "-", n["id"]).strip("-")[:40]}.png'
            loc.screenshot(path=str(dest), omit_background=True, animations='disabled')
            out[n['id']] = dest
        browser.close()
    return out
