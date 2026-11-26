"""Dump the rendered DOM tree of one top-level Elementor section: python tests/dbg_dom.py <section index> [depth]"""
import sys
from pathlib import Path

STUDIO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STUDIO / 'core'))
import verify  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

idx = int(sys.argv[1])
depth = int(sys.argv[2]) if len(sys.argv) > 2 else 4
JS = """([idx, depth]) => {
  const root = document.querySelectorAll('.elementor-element.e-parent')[idx];
  const out = [];
  const walk = (e, d) => {
    if (d > depth) return;
    const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
    const title = e.getAttribute('data-widget_type') || e.getAttribute('data-element_type');
    out.push('  '.repeat(d) + title + ' ' + Math.round(r.left) + ',' + Math.round(r.top + scrollY) + ' ' + Math.round(r.width) + 'x' + Math.round(r.height)
      + ' pad=' + cs.paddingTop + '/' + cs.paddingRight + '/' + cs.paddingBottom + '/' + cs.paddingLeft + ' mar=' + cs.marginTop + '/' + cs.marginBottom
      + ' dir=' + cs.flexDirection + ' gap=' + cs.rowGap + ' txt=' + (e.getAttribute('data-widget_type') ? (e.innerText || '').slice(0, 30).replace(/\\n/g, '|') : ''));
    for (const c of e.children) { if (c.classList.contains('elementor-element') || c.classList.contains('e-con-inner') || c.classList.contains('elementor-widget-container')) walk(c, d + 1); }
  };
  walk(root, 0);
  return out;
}"""
with sync_playwright() as p:
    b = verify.launch(p, lambda s: None)
    pg = b.new_context(viewport={'width': 1920, 'height': 900}).new_page()
    pg.goto('http://127.0.0.1:8088/', wait_until='load')
    pg.wait_for_timeout(800)
    print('\n'.join(pg.evaluate(JS, [idx, depth])))
    b.close()
