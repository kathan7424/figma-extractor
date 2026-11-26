import json, re, shutil, sys
from pathlib import Path
sys.path.insert(0, r'F:\figma-export-kit\desktop-tool')
import figma2html as f, verify as v
from playwright.sync_api import sync_playwright

out = Path(r'F:\figma-export-kit\_test\out2')
shutil.rmtree(out, ignore_errors=True)
f.convert(r'F:\figma-export-kit\_test\sample', out, log=lambda s: None)

# Build a fake "Figma render" = the HTML but with two deliberate differences CSS-gen would miss.
m = json.loads((out / 'manifest.json').read_text('utf-8'))
(out / 'renders').mkdir(exist_ok=True)
with sync_playwright() as p:
    b = v.launch(p, print)
    pg = b.new_page(device_scale_factor=2)
    for fr in m['frames']:
        hp = out / fr['html']
        css = hp.with_suffix('.css')
        orig = css.read_text('utf-8')
        tweaked = orig.replace('background-color: #f24c4c', 'background-color: #22aa55')  # badge colour
        tweaked = tweaked.replace('linear-gradient(90deg, #2457d6 0%, #8c33e6 100%)', 'linear-gradient(90deg, #d62424 0%, #e6a033 100%)')
        css.write_text(tweaked, 'utf-8')
        pg.set_viewport_size({'width': int(fr['width']), 'height': int(fr['height'])})
        pg.goto(hp.resolve().as_uri(), wait_until='networkidle')
        dest = out / 'renders' / (hp.stem + '.png')
        pg.locator('.fek-fit > *').first.screenshot(path=str(dest))
        css.write_text(orig, 'utf-8')
        fr['render'] = 'renders/' + dest.name
        fr['renderScale'] = 2
    b.close()
m['renderScale'] = 2
(out / 'manifest.json').write_text(json.dumps(m), 'utf-8')

print('--- verify only')
v.verify(out)
print('--- verify + autofix')
rows = v.verify(out, autofix=True)
print(json.dumps(json.loads((out / 'overrides.json').read_text())['1:1'], indent=1)[:600])
