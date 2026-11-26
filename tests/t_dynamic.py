"""Dynamic site test on the isolated WP (ACF PRO + Elementor): repeated Figma cards -> CPT + ACF field group + posts (+ skill checks)."""
import json
import re
import sys
from pathlib import Path

STUDIO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STUDIO / 'core'))
import dynamic  # noqa: E402
import pipeline  # noqa: E402
import wp_client  # noqa: E402

EXPORT = Path(r'F:\figma-export-kit\_real\out')
ok_all = []


def check(name, ok, detail=''):
    ok_all.append(bool(ok))
    print(('PASS  ' if ok else 'FAIL  ') + name + (f'  [{detail}]' if detail else ''))


def main():
    wp = wp_client.WP.from_file(r'F:\wp-fte-test\.studio-test-credentials')
    m, fr, node = pipeline.load_frame(EXPORT, '01 Home')
    groups = dynamic.find_repeat_groups(node)
    check('repeated card groups found in the design', len(groups) >= 1, f'{len(groups)} groups: ' + ', '.join(f'{len(c)}x{dynamic.sig(c[0], 1)[:12]}' for _, c in groups[:4]))
    parent, cards = max(groups, key=lambda g: len(g[1]))
    # section title = first text in the parent chain
    title = next((c.get('characters') for c in dynamic.kids(parent) if c.get('type') == 'TEXT'), 'Markets')
    pt, group, slot_map = dynamic.build_schema('markets', cards)
    items = dynamic.post_items(cards, slot_map)
    print(f'  post type {pt["slug"]}, {len(group["fields"])} ACF fields, {len(items)} posts')
    r = wp.schema([pt], [group])
    check('CPT registered + ACF field group imported (ACF PRO)', pt['slug'] in r['post_types'] and r['acf_active'] and r['acf_groups'], str(r))
    res = wp.posts(pt['slug'], [{'title': i['title'], 'meta': i['meta']} for i in items])
    check('posts created', len(res['ids']) == len(items), f'{len(res["ids"])} ids')
    # read back through core REST
    rest = wp._req('GET', f'wp/v2/{pt["slug"]}')
    # posts add up over repeated test runs and REST pages by 10, so "at least this run's posts (max one page)"
    check('CPT is in REST (show_in_rest)', isinstance(rest, list) and len(rest) >= min(len(items), 10), f'{len(rest) if isinstance(rest, list) else rest}')
    # ACF values stored
    first = res['ids'][0]
    pid_meta = wp._req('GET', f'wp/v2/{pt["slug"]}/{first}')
    check('post has the card title', bool(pid_meta['title']['rendered']), pid_meta['title']['rendered'][:40])

    # ---- skill checks on the front page
    html = wp.render(wp.url + '/')
    n_meta = len(re.findall(r'<meta name="DeveloperAI" content="Developer Skill">', html))
    check('skill 141: DeveloperAI meta tag exactly once in <head>', n_meta == 1, f'{n_meta}x')
    check('skill 114: no inline custom CSS / class-based CSS on elements', 'elementor-custom-css' not in html and 'data-fte-css' not in html)
    kit = wp.kit()['settings']
    check('skill 98/99: Global Colors in the Kit', len(kit.get('system_colors', [])) + len(kit.get('custom_colors', [])) >= 5,
          f'{len(kit.get("system_colors", []))}+{len(kit.get("custom_colors", []))} colors')
    check('skill 100: Global Typography in the Kit', len(kit.get('system_typography', [])) + len(kit.get('custom_typography', [])) >= 4)
    check('skill 118: global container width set', kit.get('container_width', {}).get('size') == 1720, str(kit.get('container_width')))
    info = json.loads((STUDIO / '_work' / 'last-push.json').read_text('utf-8'))
    page = wp._req('GET', f'ftes/v1/page/{info["id"]}')
    def walk(e):
        yield e
        for c in e.get('elements', []):
            yield from walk(c)
    els = [x for s in page['elementor_data'] for x in walk(s)]
    check('skill 114: no CSS classes / custom CSS fields on any element', not any(x['settings'].get('_css_classes') or x['settings'].get('css_classes') or x['settings'].get('custom_css') for x in els), f'{len(els)} elements checked')
    check('skill 113: native widgets (no HTML widget for layout)', not any(x.get('widgetType') == 'html' for x in els))
    imgs = [x['settings']['image']['url'] for x in els if x.get('widgetType') == 'image']
    check('skill 131: images served from the Media Library as WebP/SVG', imgs and all(u.endswith(('.webp', '.svg')) for u in imgs if u), f'{len(imgs)} images')
    print(f'\n{sum(ok_all)}/{len(ok_all)} passed')
    return 0 if all(ok_all) else 1


if __name__ == '__main__':
    sys.exit(main())
