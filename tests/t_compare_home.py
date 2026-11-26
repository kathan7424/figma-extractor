"""Compare the live Elementor Home page with the exact HTML build of the same Figma frame."""
import json
import sys
from pathlib import Path

STUDIO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STUDIO / 'core'))
import compare  # noqa: E402

REF = Path(r'F:\figma-export-kit\_real\out\html\desktop-layout-final\01-home-page-design.html')
OUT = STUDIO / '_work' / 'compare'


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8088/'
    r = compare.compare(REF, url, OUT, 1920, height=9721)
    for w in r['worst']:
        print(f"  y={w['y']:5d} h={w['height']:4d}  mismatch {w['mismatch']}%")
    print('files:', r['files']['diff'])


if __name__ == '__main__':
    main()
