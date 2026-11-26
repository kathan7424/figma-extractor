import json, shutil, sys, time
from pathlib import Path
sys.path.insert(0, r'F:\figma-export-kit\desktop-tool')
import figma2html as f

root = Path(r'F:\figma-export-kit\_test\big'); shutil.rmtree(root, ignore_errors=True)
(root / 'pages' / 'P').mkdir(parents=True)
(root / 'assets' / 'images').mkdir(parents=True)
def T(x, y): return [[1, 0, x], [0, 1, y]]
def solid(r, g, b): return {'type': 'SOLID', 'visible': True, 'opacity': 1, 'blendMode': 'NORMAL', 'color': {'r': r, 'g': g, 'b': b}}
def text(i, y):
    return {'id': f'{i}', 'name': f'Text {i}', 'type': 'TEXT', 'width': 300, 'height': 20, 'absoluteTransform': T(0, y),
            'characters': f'Item {i}', 'textAlignHorizontal': 'LEFT', 'textAlignVertical': 'TOP', 'textAutoResize': 'HEIGHT',
            'segments': [{'characters': f'Item {i}', 'fontName': {'family': 'Inter', 'style': 'Regular'}, 'fontSize': 14, 'fontWeight': 400,
                          'lineHeight': {'unit': 'AUTO'}, 'letterSpacing': {'unit': 'PIXELS', 'value': 0}, 'fills': [solid(.1, .1, .1)]}]}
def main():
    global root
    frames, nodes, nid = [], 0, 0
    FR, ROWS = 40, 300
    for fi in range(FR):
        kids = []
        for r in range(ROWS):
            nid += 1
            kids.append({'id': f'{fi}:{nid}', 'name': f'Row {r}', 'type': 'FRAME', 'width': 600, 'height': 40, 'absoluteTransform': T(0, r * 44),
                         'layoutMode': 'HORIZONTAL', 'itemSpacing': 8, 'counterAxisAlignItems': 'CENTER', 'fills': [solid(.95, .95, .98)], 'cornerRadius': 6,
                         'children': [dict(text(nid * 10 + k, r * 44), id=f'{fi}:{nid}:{k}') for k in range(2)] +
                                     [{'id': f'{fi}:{nid}:r', 'name': 'Dot', 'type': 'ELLIPSE', 'width': 12, 'height': 12, 'absoluteTransform': T(0, r * 44), 'fills': [solid(.9, .3, .3)]}]})
        n = {'id': f'{fi}:0', 'name': f'Page {fi}', 'type': 'FRAME', 'width': 600, 'height': ROWS * 44, 'absoluteTransform': T(0, 0),
             'absoluteBoundingBox': {'x': 0, 'y': 0, 'width': 600, 'height': ROWS * 44}, 'fills': [solid(1, 1, 1)], 'layoutMode': 'VERTICAL', 'itemSpacing': 4, 'children': kids}
        nodes += 1 + ROWS * 4
        (root / 'pages' / 'P' / f'Page {fi}.json').write_text(json.dumps(n), 'utf-8')
        frames.append({'id': f'{fi}:0', 'name': f'Page {fi}', 'page': 'P', 'type': 'FRAME', 'width': 600, 'height': ROWS * 44, 'json': f'pages/P/Page {fi}.json'})
    (root / 'manifest.json').write_text(json.dumps({'fileName': 'Big', 'frames': frames, 'images': {}}), 'utf-8')
    print('nodes:', nodes)
    out = Path(r'F:\figma-export-kit\_test\big-out'); shutil.rmtree(out, ignore_errors=True)
    t = time.time(); f.convert(root, out, log=lambda s: None); print(f'cold convert: {time.time() - t:.1f}s')
    t = time.time(); f.convert(root, out, log=lambda s: None); print(f'cached convert: {time.time() - t:.1f}s')
    t = time.time(); f.convert(root, out, log=lambda s: None, workers=1, use_cache=False, elementor=False); print(f'single process, no elementor: {time.time() - t:.1f}s')


if __name__ == '__main__':
    main()
