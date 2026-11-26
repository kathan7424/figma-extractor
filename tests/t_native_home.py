"""Build the real Buell Home page as native Elementor, push it to the isolated test site, render + compare."""
import json
import sys
import time
from pathlib import Path

STUDIO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STUDIO / 'core'))
import pipeline  # noqa: E402
import wp_client  # noqa: E402

EXPORT = Path(r'F:\figma-export-kit\_real\out')
WORK = STUDIO / '_work'


def main():
    t = time.time()
    mode = 'responsive' if '--responsive' in sys.argv else 'exact'
    res = pipeline.build(EXPORT, '01 Home', mode=mode)
    print('mode:', mode)
    WORK.mkdir(exist_ok=True)
    (WORK / 'home.elementor.json').write_text(json.dumps({'content': res['elements']}), 'utf-8')
    (WORK / 'kit.json').write_text(json.dumps(res['kit'], indent=1), 'utf-8')
    print(f'built in {time.time() - t:.1f}s  container_width={res["container_width"]}')
    print('stats', res['stats'])
    print('warnings', res['warnings'])
    print('colors', len(res['kit']['system_colors']) + len(res['kit']['custom_colors']), 'typography', len(res['kit']['system_typography']) + len(res['kit']['custom_typography']))
    print('media', len(res['media']), 'links todo', len(res['links_todo']))
    if '--push' in sys.argv:
        wp = wp_client.WP.from_file(r'F:\wp-fte-test\.studio-test-credentials')
        info = pipeline.push(res, EXPORT, wp, WORK, slug='home-native', front_page=True, fonts_dir=r'F:\figma-export-kit\_real\fonts')
        print('pushed', info)
        (WORK / 'last-push.json').write_text(json.dumps(info), 'utf-8')


if __name__ == '__main__':
    main()
