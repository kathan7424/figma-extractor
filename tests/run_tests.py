#!/usr/bin/env python3
"""FTE Studio tests. Run:  python tests/run_tests.py
Always: compile all modules, native build of the real Home frame (structure + skill rules), MCP handshake.
If the isolated test WordPress runs (http://127.0.0.1:8088): dynamic ACF/CPT + front-end skill checks.
"""
from __future__ import annotations

import json
import py_compile
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'core'))
EXPORT = Path(r'F:\figma-export-kit\_real\out')
res = []


def check(name, ok, detail=''):
    res.append(bool(ok))
    print(('PASS  ' if ok else 'FAIL  ') + name + (f'  [{detail}]' if detail else ''), flush=True)


def main():
    files = list((ROOT / 'core').glob('*.py')) + [ROOT / 'studio.py']
    bad = []
    for f in files:
        try:
            py_compile.compile(str(f), doraise=True)
        except py_compile.PyCompileError as e:
            bad.append(f'{f.name}: {e.msg[:80]}')
    check('all modules compile', not bad, '; '.join(bad) or f'{len(files)} files')

    if (EXPORT / 'manifest.json').exists():
        import pipeline
        for mode in ('exact', 'responsive'):
            r = pipeline.build(EXPORT, '01 Home', mode=mode, log=lambda s: None)

            def walk(e):
                yield e
                for c in e.get('elements', []):
                    yield from walk(c)
            els = [x for s in r['elements'] for x in walk(s)]
            check(f'{mode}: native build has sections/text/images', len(r['elements']) == 12 and r['stats'].get('text', 0) > 50, f'{len(els)} elements')
            check(f'{mode}: no custom CSS / CSS classes / HTML widgets (skill 113/114)',
                  not any(x['settings'].get('_css_classes') or x['settings'].get('css_classes') or x['settings'].get('custom_css') or x.get('widgetType') == 'html' for x in els))
            check(f'{mode}: global colours + typography + container width (skill 98-102, 118)',
                  len(r['kit']['system_colors']) + len(r['kit']['custom_colors']) >= 5 and len(r['kit']['system_typography']) >= 3 and r['kit'].get('container_width'))
            linked = sum(1 for x in els if '__globals__' in x['settings'])
            check(f'{mode}: widgets linked to globals', linked > 30, f'{linked} elements use globals')
        rp = pipeline.build(EXPORT, '01 Home', pro=True, log=lambda s: None)
        check('pro: header/footer templates + loop grids planned', rp['header'] and rp['footer'] and rp['loop_templates'],
              f'{len(rp["header"])} header, {len(rp["footer"])} footer, {len(rp["loop_templates"])} loop items')
    else:
        print('SKIP  real export not found:', EXPORT)

    p = subprocess.run([sys.executable, '-I', str(ROOT / 'core' / 'mcp_server.py')], input='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n'
                       '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n', capture_output=True, text=True, timeout=60)
    lines = [json.loads(x) for x in p.stdout.splitlines() if x.strip()]
    names = [t['name'] for t in lines[-1]['result']['tools']] if lines else []
    check('MCP server: handshake + tools', 'fte_make' in names and 'fte_compare' in names, ', '.join(names))

    try:
        urllib.request.urlopen('http://127.0.0.1:8088/', timeout=10)
        live = True
    except Exception:
        live = False
    if live:
        p = subprocess.run([sys.executable, '-I', str(ROOT / 'tests' / 't_dynamic.py')], capture_output=True, text=True, timeout=600)
        tail = [x for x in p.stdout.splitlines() if 'passed' in x]
        check('live WordPress: dynamic ACF/CPT + skill checks (t_dynamic.py)', p.returncode == 0, tail[-1] if tail else p.stdout[-200:])
    else:
        print('SKIP  live WordPress tests (start tools/test_server.py)')
    print(f'\n{sum(res)}/{len(res)} passed')
    return 0 if all(res) else 1


if __name__ == '__main__':
    sys.exit(main())
