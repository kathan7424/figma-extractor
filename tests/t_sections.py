"""Per-section layout drift of the live Elementor page vs the Figma frame (y offset and height, px)."""
import json
import sys
from pathlib import Path

STUDIO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STUDIO / 'core'))
import compare  # noqa: E402

NODE = Path(r'F:\figma-export-kit\_real\out\pages\Desktop Layout (Final)\01 Home Page Design.json')


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8088/'
    root = json.loads(NODE.read_text('utf-8'))
    ry = root['absoluteBoundingBox']['y']
    secs = sorted(((c['name'], c['absoluteBoundingBox']['y'] - ry, c['absoluteBoundingBox']['height']) for c in root['children'] if c.get('visible') is not False and not c.get('isMask')), key=lambda t: t[1])
    rows, n = compare.section_report(url, 1920, secs)
    print(f'{n} top-level sections on the page, {len(secs)} in the design')
    tot = 0
    for r in rows:
        print(f"{r['section'][:26]:26} design y={r['design_y']:5d} h={r['design_h']:5d} | page y={r['page_y']} h={r['page_h']} | dy={r['dy']} dh={r['dh']}")
        tot += abs(r['dh'] or 0)
    print('sum |dh| =', tot)


if __name__ == '__main__':
    main()
