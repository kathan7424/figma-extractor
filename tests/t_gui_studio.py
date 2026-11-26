"""Drive the real Studio GUI object: import an export folder, pick a frame, Build + send to the isolated test WordPress."""
import os
import sys
import time
from pathlib import Path

STUDIO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STUDIO))
sys.path.insert(0, str(STUDIO / 'core'))
import studio  # noqa: E402


def wait(app, t=300):
    t0 = time.time()
    while app.busy and time.time() - t0 < t:
        app.update()
        time.sleep(0.05)
    app.update()


def main():
    cred = Path(r'F:\wp-fte-test\.studio-test-credentials').read_text('utf-8').split('\n')
    app = studio.make_app()()
    app.withdraw()
    app.v['src'].set(r'F:\figma-export-kit\_real\out')
    app.v['out'].set(str(STUDIO / '_work' / 'gui-out'))
    app.v['fonts'].set(r'F:\figma-export-kit\_real\fonts')
    app.v['url'].set(cred[0]); app.v['user'].set(cred[1]); app.v['pw'].set(cred[2])
    app.act_import(); wait(app)
    frames = app.tree.get_children()
    print('GUI import:', 'PASS' if app.manifest and len(frames) >= 1 else 'FAIL', f'{len(frames)} frame(s)')
    app.tree.selection_set(frames[0])
    app.act_connect(); wait(app)
    print('GUI connect:', 'PASS' if 'Connected' in app.conn_lbl.cget('text') else 'FAIL', app.conn_lbl.cget('text'))
    app.act_build(True); wait(app, 600)
    print('GUI build+push:', 'PASS' if app.link else 'FAIL', app.link, '|', app.res_lbl.cget('text')[:120])
    app.destroy()


if __name__ == '__main__':
    main()
