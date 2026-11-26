#!/usr/bin/env python3
"""
FTE Studio MCP server (stdio, stdlib only). Lets Claude drive the whole Figma -> HTML -> Elementor/ACF pipeline.

Register (Claude Code / Claude desktop):   see docs/GUIDE.html  or run  python studio.py mcp-config
WordPress credentials are read from env (FTE_WP_URL / FTE_WP_USER / FTE_WP_PASS) so they never appear in the chat.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

TOOLS = [
    {'name': 'fte_extract', 'description': 'EXTRACT ONLY (no WordPress): Figma .fig/zip -> exact HTML/CSS + design JSON + tokens + assets + figma-extract.zip. Short summary.',
     'inputSchema': {'type': 'object', 'required': ['source', 'out'], 'properties': {
         'source': {'type': 'string'}, 'out': {'type': 'string'}, 'pages': {'type': 'array', 'items': {'type': 'string'}}, 'fonts_dir': {'type': 'string'}}}},
    {'name': 'fte_make_site', 'description': 'WHOLE WordPress site from an extracted folder: every desktop frame -> its own page (Home = front page), Main Menu; pro=true adds Theme Builder header/footer + Loop Grids + ACF. Credentials from env. Short summary.',
     'inputSchema': {'type': 'object', 'required': ['out'], 'properties': {
         'out': {'type': 'string'}, 'frames': {'type': 'array', 'items': {'type': 'string'}, 'description': 'optional name filters'},
         'pro': {'type': 'boolean'}, 'fonts_dir': {'type': 'string'}}}},
    {'name': 'fte_make', 'description': 'ONE call does everything: Figma file (.fig/zip/folder) -> exact HTML/CSS + JSON + assets -> native Elementor page (global colours/fonts, WebP) -> WordPress (credentials from env) -> accuracy check. Returns a SHORT summary (cheap in tokens). Use this first; use the other tools only to refine.',
     'inputSchema': {'type': 'object', 'required': ['source', 'out'], 'properties': {
         'source': {'type': 'string'}, 'out': {'type': 'string'}, 'frame': {'type': 'string', 'description': 'frame name part (default: the biggest frame)'},
         'push': {'type': 'boolean', 'description': 'send to WordPress'}, 'pro': {'type': 'boolean', 'description': 'Elementor Pro: Theme Builder + Loop Grid + ACF'},
         'fonts_dir': {'type': 'string'}, 'responsive': {'type': 'boolean'}}}},
    {'name': 'fte_import', 'description': 'Read a .fig file (offline) or a Figma-plugin export and produce exact HTML/CSS + JSON + assets. Returns the frame list.',
     'inputSchema': {'type': 'object', 'required': ['source', 'out'], 'properties': {
         'source': {'type': 'string', 'description': '.fig file, plugin .zip or export folder'}, 'out': {'type': 'string'},
         'pages': {'type': 'array', 'items': {'type': 'string'}}, 'frames': {'type': 'array', 'items': {'type': 'string'}},
         'fonts_dir': {'type': 'string'}}}},
    {'name': 'fte_list_frames', 'description': 'List frames of an output folder.', 'inputSchema': {'type': 'object', 'required': ['out'], 'properties': {'out': {'type': 'string'}}}},
    {'name': 'fte_frame_brief', 'description': 'Markdown outline of a frame (sizes, colours, fonts, texts, images).',
     'inputSchema': {'type': 'object', 'required': ['out', 'frame'], 'properties': {'out': {'type': 'string'}, 'frame': {'type': 'string'}}}},
    {'name': 'fte_build_elementor', 'description': 'Build NATIVE Elementor (containers/widgets, global colours/typography, no custom CSS) from a frame. push=true uploads WebP media, sets the Kit and creates the page on WordPress (credentials from env).',
     'inputSchema': {'type': 'object', 'required': ['out', 'frame'], 'properties': {
         'out': {'type': 'string'}, 'frame': {'type': 'string'}, 'push': {'type': 'boolean'}, 'slug': {'type': 'string'},
         'front_page': {'type': 'boolean'}, 'pro': {'type': 'boolean', 'description': 'Elementor Pro installed (Theme Builder / Loop Grid / dynamic tags)'}}}},
    {'name': 'fte_compare', 'description': 'Accuracy: compare the live WordPress page with the exact HTML build of the frame (match %, worst bands).',
     'inputSchema': {'type': 'object', 'required': ['out', 'frame', 'url'], 'properties': {'out': {'type': 'string'}, 'frame': {'type': 'string'}, 'url': {'type': 'string'}}}},
    {'name': 'fte_wp_info', 'description': 'Check the WordPress connection: versions of WordPress / Elementor / Elementor Pro / ACF.', 'inputSchema': {'type': 'object', 'properties': {}}},
]


def find_frame(out, q):
    m = json.loads((Path(out) / 'manifest.json').read_text('utf-8'))
    ql = str(q).lower()
    for f in m['frames']:
        if f['id'] == q or ql in f['name'].lower():
            return m, f
    raise ValueError(f'frame "{q}" not found')


def call(name, a, log):
    import figma2html
    if name == 'fte_extract':
        import studio
        return studio.do_extract(a['source'], a['out'], a.get('fonts_dir'), a.get('pages'), True, log=lambda s: None)
    if name == 'fte_make_site':
        import studio
        import wp_client
        return studio.do_make_site(a['out'], wp_client.WP(), a.get('frames'), bool(a.get('pro')), a.get('fonts_dir'), log=lambda s: None)
    if name == 'fte_make':
        import studio
        wp = None
        if a.get('push'):
            import wp_client
            wp = wp_client.WP()
        return studio.do_make(a['source'], a['out'], a.get('frame'), wp, a.get('fonts_dir'), True, bool(a.get('pro')),
                              'responsive' if a.get('responsive') else 'exact', True, log=lambda s: None)
    if name == 'fte_import':
        src = Path(a['source'])
        if src.suffix.lower() == '.fig':
            import fig_import
            fig_import.export(src, a['out'], a.get('pages'), a.get('frames'), log=log)
            src = Path(a['out'])
        figma2html.convert(src, a['out'], elementor=False, fonts_dir=a.get('fonts_dir'), log=log)
        return call('fte_list_frames', {'out': a['out']}, log)
    if name == 'fte_list_frames':
        m = json.loads((Path(a['out']) / 'manifest.json').read_text('utf-8'))
        fr = m['frames']
        lines = [f'{m.get("fileName")}: {len(fr)} frames'] + [f'{f["name"]} | {f.get("page", "")} | {f["width"]:.0f}x{f["height"]:.0f}' for f in fr[:40]]
        return '\n'.join(lines + ([f'... +{len(fr) - 40} more'] if len(fr) > 40 else []))
    if name == 'fte_frame_brief':
        m, f = find_frame(a['out'], a['frame'])
        node = json.loads((Path(a['out']) / f['json']).read_text('utf-8'))
        return figma2html.handoff_md(f, node, m.get('images', {}), max_depth=3, max_lines=120)   # short on purpose (tokens)
    if name == 'fte_build_elementor':
        import pipeline
        res = pipeline.build(a['out'], a['frame'], pro=bool(a.get('pro')), log=log)
        summary = {'stats': res['stats'], 'warnings': res['warnings'], 'links_todo': res['links_todo'], 'container_width': res['container_width']}
        if a.get('push'):
            import wp_client
            info = pipeline.push(res, a['out'], wp_client.WP(), Path(a['out']) / '_wp', slug=a.get('slug'), front_page=bool(a.get('front_page')), log=log)
            summary['page'] = info
        else:
            work = Path(a['out']) / '_wp'
            work.mkdir(exist_ok=True)
            (work / 'page.elementor.json').write_text(json.dumps({'content': res['elements']}), 'utf-8')
            (work / 'kit.json').write_text(json.dumps(res['kit'], indent=1), 'utf-8')
            summary['files'] = [str(work / 'page.elementor.json'), str(work / 'kit.json')]
        return json.dumps(summary, indent=1)
    if name == 'fte_compare':
        import compare
        m, f = find_frame(a['out'], a['frame'])
        ref = Path(a['out']) / f['html']
        r = compare.compare(ref, a['url'], Path(a['out']) / '_compare', int(f['width']), log=log)
        return json.dumps({k: r[k] for k in ('match', 'reference_height', 'candidate_height', 'worst', 'files')}, indent=1)
    if name == 'fte_wp_info':
        import wp_client
        return json.dumps(wp_client.WP().info(), indent=1)
    raise ValueError(f'unknown tool {name}')


def reply(i, result=None, error=None):
    msg = {'jsonrpc': '2.0', 'id': i}
    msg['error' if error else 'result'] = error or result
    sys.__stdout__.write(json.dumps(msg) + '\n')
    sys.__stdout__.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        i, method, params = req.get('id'), req.get('method'), req.get('params') or {}
        if i is None:
            continue
        if method == 'initialize':
            reply(i, {'protocolVersion': params.get('protocolVersion', '2024-11-05'), 'capabilities': {'tools': {}},
                      'serverInfo': {'name': 'fte-studio', 'version': '1.0.0'}})
        elif method == 'ping':
            reply(i, {})
        elif method == 'tools/list':
            reply(i, {'tools': TOOLS})
        elif method == 'tools/call':
            logs = []
            sys.stdout = sys.stderr
            try:
                text = call(params.get('name'), params.get('arguments') or {}, logs.append)
                if logs and params.get('name') not in ('fte_list_frames', 'fte_frame_brief'):
                    text += '\n\n--- log ---\n' + '\n'.join(logs[-20:])
                reply(i, {'content': [{'type': 'text', 'text': text}]})
            except Exception as e:
                reply(i, {'content': [{'type': 'text', 'text': f'ERROR: {e}\n{traceback.format_exc()[-1200:]}'}], 'isError': True})
            finally:
                sys.stdout = sys.__stdout__
        else:
            reply(i, error={'code': -32601, 'message': f'method not found: {method}'})


if __name__ == '__main__':
    main()
