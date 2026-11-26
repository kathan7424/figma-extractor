#!/usr/bin/env python3
"""Build the small distributable: dist/FTE-Studio.zip (code + docs + plugins; no venv, no work files)."""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {'.venv', '_work', 'dist', '__pycache__', '.browsers'}
SKIP_FILES = {'fte-studio-bridge.zip'}


def main():
    dist = ROOT / 'dist'
    dist.mkdir(exist_ok=True)
    out = dist / 'FTE-Studio.zip'
    n = 0
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in sorted(ROOT.rglob('*')):
            rel = f.relative_to(ROOT)
            if f.is_dir() or any(p in SKIP_DIRS for p in rel.parts) or f.name in SKIP_FILES or f.suffix == '.pyc':
                continue
            z.write(f, Path('FTE-Studio') / rel)
            n += 1
    print(f'{out}  {n} files  {out.stat().st_size / 1024:.0f} KB')


if __name__ == '__main__':
    main()
