"""Side-by-side crop of the last comparison: python tests/view.py <y0> <y1> [scale]  ->  _work/compare/view.png"""
import sys
from pathlib import Path
from PIL import Image

d = Path(__file__).resolve().parent.parent / '_work' / 'compare'
y0, y1 = int(sys.argv[1]), int(sys.argv[2])
s = float(sys.argv[3]) if len(sys.argv) > 3 else 0.4
ims = [Image.open(d / f).convert('RGB') for f in ('reference.png', 'candidate.png')]
crops = []
for im in ims:
    c = im.crop((0, y0, im.width, min(y1, im.height)))
    crops.append(c.resize((round(c.width * s), round(c.height * s))))
w = sum(c.width for c in crops) + 12
out = Image.new('RGB', (w, max(c.height for c in crops)), (255, 0, 255))
x = 0
for c in crops:
    out.paste(c, (x, 0))
    x += c.width + 12
out.save(d / 'view.png')
print(out.size)
