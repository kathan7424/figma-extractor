#!/usr/bin/env python3
"""
FTE Studio - Figma -> exact HTML + native Elementor (ACF / Elementor Pro ready).  One program: desktop app + command line.

  Studio.bat                      (double-click)  = desktop app
  python studio.py doctor         check this computer
  python studio.py import FILE.fig -o OUT [--fonts DIR]
  python studio.py extract FILE.fig -o OUT        <- EXTRACT ONLY: HTML/CSS + JSON + assets (+zip), no WordPress
  python studio.py site OUT [--pro]               <- WHOLE site: all desktop frames -> pages + menu
  python studio.py make FILE.fig -o OUT [--frame NAME] [--push] [--pro]   <- ONE step, short summary
  python studio.py build OUT FRAME [--push] [--front-page]      (WordPress credentials: env FTE_WP_URL / FTE_WP_USER / FTE_WP_PASS)
  python studio.py plugin-zip     make fte-studio-bridge.zip to upload in WP Admin
  python studio.py mcp-config     print the Claude MCP config
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import traceback
import webbrowser
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'core'))
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / '.browsers'))
CONFIG = Path.home() / '.fte-studio.json'   # remembers URL + username only. The password is NEVER saved.


# ----------------------------------------------------------------------------- actions (shared by GUI and CLI)

def do_import(src, out, fonts=None, log=print, pages=None):
    import figma2html
    src = Path(src)
    if src.suffix.lower() == '.fig':
        import fig_import
        log('.fig file offline vanchi rahyo chu ...')
        fig_import.export(src, out, pages or None, log=log)
        src = Path(out)
    figma2html.convert(src, out, elementor=False, fonts_dir=fonts or None, log=log)
    return json.loads((Path(out) / 'manifest.json').read_text('utf-8'))


EXTRACT_PARTS = ('manifest.json', 'tokens.json', 'tokens.css', 'index.html', 'html', 'pages', 'assets', 'fonts', 'handoff', 'renders')


def make_extract_zip(out):
    """Everything a developer needs from the Figma file, nothing WordPress: HTML/CSS, design JSON, tokens, assets, fonts."""
    out = Path(out)
    dest = out / 'figma-extract.zip'
    import zipfile
    with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as z:
        for part in EXTRACT_PARTS:
            src = out / part
            files = [src] if src.is_file() else (sorted(x for x in src.rglob('*') if x.is_file()) if src.is_dir() else [])
            for f in files:
                z.write(f, f.relative_to(out))
    return dest


def do_extract(src, out, fonts=None, pages=None, zip_it=True, log=print):
    """EXTRACT ONLY (no WordPress): Figma -> exact HTML/CSS + design JSON + tokens + assets (+ zip). Returns a short summary."""
    m = do_import(src, out, fonts, log, pages)
    out = Path(out)
    n_img = len(list((out / 'assets' / 'images').glob('*'))) if (out / 'assets' / 'images').exists() else 0
    n_svg = len(list((out / 'assets' / 'svg').glob('*.svg'))) if (out / 'assets' / 'svg').exists() else 0
    lines = [f'{m.get("fileName")}: {len(m["frames"])} frames extracted',
             f'HTML/CSS: {out / "html"}   (open {out / "index.html"})',
             f'Design JSON: {out / "pages"}  + tokens.json / tokens.css',
             f'Assets: {n_img} images, {n_svg} SVG  ->  {out / "assets"}']
    if zip_it:
        z = make_extract_zip(out)
        lines.append(f'ZIP: {z}  ({z.stat().st_size / 1048576:.1f} MB)')
    return '\n'.join(lines)


def page_title(name):
    import re
    t = re.sub(r'^\s*\d+[\s._-]*', '', name).strip()
    t = re.sub(r'\b(page\s*design|design|desktop|- desktop)\b', '', t, flags=re.I).strip(' -_')
    return t or name


def do_make_site(out, wp, frames=None, pro=False, fonts=None, min_width=1200, log=lambda s: None):
    """FULL SITE: every desktop frame -> its own WordPress page, Home = front page, one Main Menu. Pro: header/footer once (Theme Builder).
    Returns a short summary (token-cheap)."""
    import re
    import pipeline
    m = json.loads((Path(out) / 'manifest.json').read_text('utf-8'))
    pick = [f for f in m['frames'] if f['width'] >= min_width and (not frames or any(q.lower() in f['name'].lower() for q in frames))]
    if not pick:
        return 'Koi desktop frame nathi malyu (width >= %d).' % min_width
    home = next((f for f in pick if re.search(r'home', f['name'], re.I)), pick[0])
    pick = [home] + [f for f in pick if f is not home]
    work = Path(out) / '_wp'
    work.mkdir(exist_ok=True)
    made, lines = [], []
    for i, f in enumerate(pick):
        res = pipeline.build(out, f['id'], pro=pro, log=log)
        if i > 0:     # header/footer templates only once (from Home)
            res['header'], res['footer'] = [], []
        title = page_title(f['name'])
        res['title'] = title
        info = pipeline.push(res, out, wp, work, slug=pipeline.en.slug(title), front_page=(f is home), fonts_dir=fonts if i == 0 else None, log=log)
        made.append((title, info))
        lines.append(f'{title}: {info["link"]}')
    menu = wp.menu('Main Menu', [{'title': t, 'page_id': inf['id']} for t, inf in made], location='menu-1')
    lines.insert(0, f'Site: {len(made)} pages, Main Menu id {menu.get("menu_id")}, front page = {made[0][0]}, pro={pro}')
    return '\n'.join(lines)


def do_build(out, frame, wp=None, front_page=False, pro=False, fonts=None, log=print):
    import pipeline
    res = pipeline.build(out, frame, pro=pro, log=log)
    work = Path(out) / '_wp'
    work.mkdir(exist_ok=True)
    (work / f'{pipeline.en.slug(frame)}.elementor.json').write_text(json.dumps({'content': res['elements']}), 'utf-8')
    (work / 'kit.json').write_text(json.dumps(res['kit'], indent=1), 'utf-8')
    log(f'  built: {res["stats"]}')
    if res['warnings']:
        log(f'  notes: {res["warnings"]}')
    info = None
    if wp:
        info = pipeline.push(res, out, wp, work, slug=pipeline.en.slug(res['title']), front_page=front_page, fonts_dir=fonts, log=log)
        log(f'  page: {info["link"]}')
    return res, info


def do_make(src, out, frame=None, wp=None, fonts=None, front_page=True, pro=False, mode='exact', check=True, log=lambda s: None):
    """ONE step: Figma file -> exact HTML + native Elementor (+ WordPress) + accuracy. Returns a SHORT text summary (token-cheap)."""
    m = json.loads((Path(out) / 'manifest.json').read_text('utf-8')) if (Path(out) / 'manifest.json').exists() and Path(src).resolve() == Path(out).resolve() \
        else do_import(src, out, fonts, log)
    frames = m['frames']
    f = next((x for x in frames if frame and (x['id'] == frame or frame.lower() in x['name'].lower())), None) if frame else \
        max(frames, key=lambda x: x['width'] * x['height'])
    if not f:
        return f'Frame "{frame}" nathi malyu. Frames: ' + ', '.join(x['name'] for x in frames[:12])
    import pipeline
    res = pipeline.build(out, f['id'], pro=pro, mode=mode, log=log)
    work = Path(out) / '_wp'
    work.mkdir(exist_ok=True)
    info = None
    if wp:
        info = pipeline.push(res, out, wp, work, slug=pipeline.en.slug(res['title']), front_page=front_page, fonts_dir=fonts, log=log)
    else:
        (work / 'page.elementor.json').write_text(json.dumps({'content': res['elements']}), 'utf-8')
        (work / 'kit.json').write_text(json.dumps(res['kit'], indent=1), 'utf-8')
    s = res['stats']
    lines = [f'{f["name"]} {f["width"]:.0f}x{f["height"]:.0f}  mode={mode}  pro={pro}',
             f'HTML (exact): {Path(out) / f["html"]}',
             f'Elementor: {s.get("text", 0)} text, {s.get("button", 0)} buttons, {s.get("image", 0) + s.get("asset", 0) + s.get("mask_raster", 0)} images, '
             f'{len(res["kit"]["system_colors"]) + len(res["kit"]["custom_colors"])} global colours, {len(res["kit"]["system_typography"]) + len(res["kit"]["custom_typography"])} global fonts']
    if info:
        lines.append(f'Page: {info["link"]}   Editor: {info["edit"]}')
        if check:
            try:
                import compare
                r = compare.compare(Path(out) / f['html'], info['link'], Path(out) / '_compare', int(f['width']), log=lambda s: None)
                lines.append(f'Accuracy vs exact HTML: {r["match"]}% (tolerant) / {r["strict_match"]}% (strict pixel); worst band y={r["worst"][0]["y"]} {r["worst"][0]["mismatch"]}%')
            except Exception as e:
                lines.append(f'Accuracy check skipped: {str(e)[:80]}')
    else:
        lines.append(f'Elementor JSON: {work / "page.elementor.json"}  (WordPress connect karo to push thase)')
    if res['links_todo']:
        lines.append('Links to set: ' + ', '.join(res['links_todo'][:8]))
    if res['warnings']:
        lines.append('Notes: ' + '; '.join(f'{k} x{v}' for k, v in list(res['warnings'].items())[:3]))
    return '\n'.join(lines)


def make_plugin_zip(dest=None):
    dest = Path(dest or ROOT / 'fte-studio-bridge.zip')
    src = ROOT / 'wp-plugin' / 'fte-studio-bridge'
    with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in src.rglob('*'):
            if f.is_file():
                z.write(f, Path('fte-studio-bridge') / f.relative_to(src))
    return dest


def doctor(log=print, wp=None):
    ok = True

    def row(name, good, detail=''):
        nonlocal ok
        ok = ok and good
        log(('  OK   ' if good else '  FIX  ') + name + (f'   {detail}' if detail else ''))
    v = sys.version_info
    row('Python', v >= (3, 10), f'{v.major}.{v.minor}.{v.micro}' + ('' if v >= (3, 14) else '  (3.14+ joiye .fig vanchva mate; plugin-zip 3.10+ ma chale)'))
    try:
        import PIL
        row('Pillow (WebP images)', True, PIL.__version__)
    except ImportError:
        row('Pillow (WebP images)', False, 'Setup.bat chalavo')
    try:
        import playwright  # noqa: F401
        import verify
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = verify.launch(p, lambda s: None)
            b.close()
        row('Browser for accuracy check (Edge/Chrome)', True)
    except Exception as e:
        row('Browser for accuracy check (Edge/Chrome)', False, str(e)[:70])
    if wp:
        try:
            i = wp.info()
            row('WordPress connection', True, wp.url)
            row('Elementor', bool(i.get('elementor')), str(i.get('elementor')))
            log(('  OK   ' if i.get('elementor_pro') else '  --   ') + 'Elementor Pro (optional)   ' + str(i.get('elementor_pro') or 'not installed: Theme Builder / Loop Grid skip thase'))
            row('ACF', bool(i.get('acf')), f'{i.get("acf")}  pro={i.get("acf_pro")}')
        except Exception as e:
            row('WordPress connection', False, str(e)[:90])
    log('READY' if ok else 'Upar "FIX" vali vastu sudharo.')
    return ok


MCP_CONFIG = {'mcpServers': {'fte-studio': {'command': sys.executable, 'args': [str(ROOT / 'core' / 'mcp_server.py')],
                                            'env': {'FTE_WP_URL': 'https://your-site.test', 'FTE_WP_USER': 'admin', 'FTE_WP_PASS': '<Application Password>'}}}}


# ----------------------------------------------------------------------------- desktop app

def make_app():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title('FTE Studio - Figma to WordPress')
            self.geometry('900x680')
            self.minsize(780, 560)
            self.q = queue.Queue()
            self.busy = False
            self.manifest = None
            self.wp = None
            cfg = {}
            try:
                cfg = json.loads(CONFIG.read_text('utf-8'))
            except Exception:
                pass
            self.v = {k: tk.StringVar(value=cfg.get(k, d)) for k, d in (('src', ''), ('out', ''), ('fonts', ''), ('url', ''), ('user', ''), ('pw', ''))}
            self.v_front = tk.BooleanVar(value=True)
            self.v_pro = tk.BooleanVar(value=False)
            self.build_ui()
            self.after(100, self.poll)

        # ---- layout
        def build_ui(self):
            style = ttk.Style(self)
            try:
                style.theme_use('vista')
            except tk.TclError:
                pass
            nb = ttk.Notebook(self)
            nb.pack(fill='both', expand=True, padx=8, pady=(8, 0))
            t0 = ttk.Frame(nb, padding=18)
            t1, t2, t3 = ttk.Frame(nb, padding=12), ttk.Frame(nb, padding=12), ttk.Frame(nb, padding=12)
            nb.add(t0, text='  Start  ')
            nb.add(t1, text='  1. Design  ')
            nb.add(t2, text='  2. WordPress  ')
            nb.add(t3, text='  3. Build  ')
            # ---- Start screen: two clear paths
            style.configure('Big.TButton', font=('Segoe UI', 12, 'bold'), padding=(16, 14))
            ttk.Label(t0, text='Shu karvu che?', font=('Segoe UI', 15, 'bold')).pack(anchor='w', pady=(0, 12))
            c1 = ttk.LabelFrame(t0, text=' A. Fakt Figma extract (WordPress vagar) ', padding=14)
            c1.pack(fill='x', pady=6)
            ttk.Label(c1, text='Exact HTML + CSS  ·  design JSON (dar frame)  ·  colours/fonts (tokens)  ·  images (full quality)  ·  icons (SVG)  ·  ek ZIP',
                      wraplength=820).pack(anchor='w')
            ttk.Button(c1, text='Extract karo  ->', style='Big.TButton', command=lambda: nb.select(t1)).pack(anchor='w', pady=(10, 0))
            c2 = ttk.LabelFrame(t0, text=' B. WordPress site banavo (Elementor + ACF) ', padding=14)
            c2.pack(fill='x', pady=6)
            ttk.Label(c2, text='1) Design extract  2) WordPress connect (helper plugin + Application Password)  3) Ek page ke AAKHI site build + accuracy check. '
                               'Pro hoy to Header/Footer Theme Builder ma ane repeated cards Loop Grid + ACF.', wraplength=820).pack(anchor='w')
            ttk.Button(c2, text='WordPress setup  ->', style='Big.TButton', command=lambda: nb.select(t2)).pack(anchor='w', pady=(10, 0))
            ttk.Label(t0, text='Claude sathe: docs\\GUIDE.html -> "Claude sathe (MCP)". Claude tools: fte_extract, fte_make_site, fte_make.',
                      foreground='#666').pack(anchor='w', pady=(14, 0))
            self.nb = nb

            def entry(parent, r, label, key, browse=None, show=None, hint=None):
                ttk.Label(parent, text=label).grid(row=r, column=0, sticky='w', pady=4)
                ttk.Entry(parent, textvariable=self.v[key], show=show).grid(row=r, column=1, sticky='ew', padx=8)
                if browse:
                    ttk.Button(parent, text='Browse', command=browse).grid(row=r, column=2)
                if hint:
                    ttk.Label(parent, text=hint, foreground='#777').grid(row=r + 1, column=1, sticky='w', padx=8)
            for t in (t1, t2, t3):
                t.columnconfigure(1, weight=1)
            entry(t1, 0, 'Figma file (.fig / .zip / folder)', 'src', self.pick_src, hint='Figma plugin ni zip ke seedhi .fig file - banne chale')
            entry(t1, 2, 'Output folder', 'out', self.pick_out)
            entry(t1, 4, 'Fonts folder (optional)', 'fonts', self.pick_fonts, hint='Design ma vaparela font files (otf/ttf/woff2)')
            b1 = ttk.Frame(t1)
            b1.grid(row=6, column=0, columnspan=3, sticky='w', pady=10)
            self.btn_import = ttk.Button(b1, text='Extract only:  HTML/CSS + design JSON + assets', command=self.act_import)
            self.btn_import.pack(side='left')
            ttk.Button(b1, text='Open output folder', command=lambda: self.v['out'].get() and os.startfile(self.v['out'].get())).pack(side='left', padx=8)
            ttk.Button(b1, text='Open preview (index.html)', command=lambda: self.v['out'].get() and webbrowser.open((Path(self.v['out'].get()) / 'index.html').as_uri())).pack(side='left')
            cols = ('name', 'page', 'size')
            self.tree = ttk.Treeview(t1, columns=cols, show='headings', height=11, selectmode='browse')
            for c, w in (('name', 380), ('page', 260), ('size', 90)):
                self.tree.heading(c, text=c.title())
                self.tree.column(c, width=w)
            self.tree.grid(row=7, column=0, columnspan=3, sticky='nsew')
            t1.rowconfigure(7, weight=1)
            self.tree.bind('<Double-1>', lambda e: self.open_html())
            ttk.Label(t1, text='Frame par double-click = HTML preview (exact design)', foreground='#777').grid(row=8, column=0, columnspan=3, sticky='w', pady=4)

            entry(t2, 0, 'WordPress URL', 'url', hint='jem ke https://mysite.com  (pela staging site vapro)')
            entry(t2, 2, 'Username', 'user')
            entry(t2, 4, 'Application Password', 'pw', show='*', hint='WP Admin > Users > Profile > Application Passwords. Aa save nathi thatu.')
            bar = ttk.Frame(t2)
            bar.grid(row=6, column=0, columnspan=3, sticky='w', pady=10)
            ttk.Button(bar, text='Test connection', command=self.act_connect).pack(side='left')
            ttk.Button(bar, text='Make helper plugin zip', command=self.act_zip).pack(side='left', padx=8)
            self.conn_lbl = ttk.Label(t2, text='Helper plugin "FTE Studio Bridge" site par install karvo padse (zip upar thi banavo).', wraplength=760, foreground='#555')
            self.conn_lbl.grid(row=7, column=0, columnspan=3, sticky='w')

            ttk.Label(t3, text='Selected frame  ->  native Elementor page (global colours + fonts, no custom CSS, WebP images)', wraplength=780).grid(row=0, column=0, columnspan=3, sticky='w')
            ttk.Checkbutton(t3, text='Set as front page', variable=self.v_front).grid(row=1, column=0, sticky='w', pady=6)
            ttk.Checkbutton(t3, text='Elementor Pro installed', variable=self.v_pro).grid(row=1, column=1, sticky='w')
            bar3 = ttk.Frame(t3)
            bar3.grid(row=2, column=0, columnspan=3, sticky='w', pady=6)
            self.btn_local = ttk.Button(bar3, text='Build JSON only (no WordPress)', command=lambda: self.act_build(False))
            self.btn_local.pack(side='left')
            self.btn_push = ttk.Button(bar3, text='Build + send to WordPress', command=lambda: self.act_build(True))
            self.btn_push.pack(side='left', padx=8)
            ttk.Button(bar3, text='Check accuracy', command=self.act_compare).pack(side='left')
            ttk.Button(bar3, text='Build WHOLE site (all desktop frames + menu)', command=self.act_site).pack(side='left', padx=8)
            self.res_lbl = ttk.Label(t3, text='', wraplength=780, foreground='#1d7a3a')
            self.res_lbl.grid(row=3, column=0, columnspan=3, sticky='w', pady=6)
            self.link = None
            ttk.Button(t3, text='Open page', command=lambda: self.link and webbrowser.open(self.link)).grid(row=4, column=0, sticky='w')
            ttk.Button(t3, text='Open in Elementor editor', command=lambda: self.edit and webbrowser.open(self.edit)).grid(row=4, column=1, sticky='w')
            self.edit = None

            self.log = tk.Text(self, height=9, wrap='word', state='disabled', font=('Consolas', 9))
            self.log.pack(fill='x', padx=8, pady=8)
            self.pb = ttk.Progressbar(self, mode='indeterminate')
            self.pb.pack(fill='x', padx=8, pady=(0, 8))

        # ---- helpers
        def pick_src(self):
            p = filedialog.askopenfilename(filetypes=[('Figma', '*.fig *.zip'), ('All', '*.*')]) or filedialog.askdirectory()
            if p:
                self.v['src'].set(p)
                if not self.v['out'].get():
                    self.v['out'].set(str(Path(p).with_suffix('')) + '-studio')

        def pick_out(self):
            p = filedialog.askdirectory()
            if p:
                self.v['out'].set(p)

        def pick_fonts(self):
            p = filedialog.askdirectory()
            if p:
                self.v['fonts'].set(p)

        def say(self, s):
            self.log.configure(state='normal')
            self.log.insert('end', str(s) + '\n')
            self.log.see('end')
            self.log.configure(state='disabled')

        def run(self, fn, done=None):
            if self.busy:
                return
            self.busy = True
            self.pb.start(12)

            def work():
                try:
                    r = fn(lambda s: self.q.put(('log', s)))
                    self.q.put(('done', (done, r)))
                except Exception as e:
                    self.q.put(('log', f'ERROR: {e}'))
                    self.q.put(('log', traceback.format_exc()[-900:]))
                    self.q.put(('done', (None, None)))
            threading.Thread(target=work, daemon=True).start()

        def poll(self):
            try:
                while True:
                    k, v = self.q.get_nowait()
                    if k == 'log':
                        self.say(v)
                    else:
                        self.busy = False
                        self.pb.stop()
                        cb, r = v
                        if cb:
                            cb(r)
            except queue.Empty:
                pass
            self.after(100, self.poll)

        def need(self, *keys):
            miss = [k for k in keys if not self.v[k].get().strip()]
            if miss:
                messagebox.showwarning('Missing', 'Pela aa bharo: ' + ', '.join(miss))
                return False
            return True

        def make_wp(self):
            import wp_client
            self.wp = wp_client.WP(self.v['url'].get().strip(), self.v['user'].get().strip(), self.v['pw'].get())
            try:
                CONFIG.write_text(json.dumps({'url': self.v['url'].get().strip(), 'user': self.v['user'].get().strip(),
                                              'src': self.v['src'].get(), 'out': self.v['out'].get(), 'fonts': self.v['fonts'].get()}), 'utf-8')
            except OSError:
                pass
            return self.wp

        # ---- actions
        def act_import(self):
            if not self.need('src', 'out'):
                return
            src, out, fonts = self.v['src'].get(), self.v['out'].get(), self.v['fonts'].get() or None   # read Tk vars in the main thread
            def fn(log):
                summary = do_extract(src, out, fonts, None, True, log)
                log(summary)
                return json.loads((Path(out) / 'manifest.json').read_text('utf-8'))
            self.run(fn, self.after_import)

        def after_import(self, m):
            self.manifest = m
            self.tree.delete(*self.tree.get_children())
            for f in m['frames']:
                self.tree.insert('', 'end', iid=f['id'], values=(f['name'], f.get('page', ''), f'{f["width"]:.0f}x{f["height"]:.0f}'))
            self.say(f'Done: {len(m["frames"])} frames. figma-extract.zip ready. WordPress mate: 2. WordPress -> 3. Build.')

        def selected(self):
            sel = self.tree.selection()
            if not sel:
                messagebox.showinfo('Frame', '1. Design tab ma ek frame select karo.')
                return None
            return sel[0]

        def open_html(self):
            fid = self.selected()
            if fid and self.manifest:
                f = next(x for x in self.manifest['frames'] if x['id'] == fid)
                webbrowser.open((Path(self.v['out'].get()) / f['html']).as_uri())

        def act_connect(self):
            if not self.need('url', 'user', 'pw'):
                return
            wp = self.make_wp()

            def fn(log):
                import io
                buf = []
                doctor(buf.append, wp)
                for b in buf:
                    log(b)
                return wp.info()
            self.run(fn, lambda i: (self.v_pro.set(bool(i.get('elementor_pro'))), self.conn_lbl.configure(
                text=f'Connected. WordPress {i["wordpress"]} · Elementor {i["elementor"]} · Pro {i["elementor_pro"] or "no"} · ACF {i["acf"]}')))

        def act_zip(self):
            z = make_plugin_zip()
            self.say(f'Plugin zip: {z}')
            messagebox.showinfo('Helper plugin', f'{z}\n\nWP Admin > Plugins > Add New > Upload Plugin, ane Activate karo.')
            os.startfile(z.parent)

        def act_build(self, push):
            fid = self.selected()
            if not fid or not self.need('out'):
                return
            wp = None
            if push:
                if not self.need('url', 'user', 'pw'):
                    return
                wp = self.make_wp()
            f = next(x for x in self.manifest['frames'] if x['id'] == fid)
            out, front, pro, fonts = self.v['out'].get(), self.v_front.get(), self.v_pro.get(), self.v['fonts'].get() or None

            def fn(log):
                return do_build(out, f['id'], wp, front, pro, fonts, log)

            def done(r):
                res, info = r
                s = res['stats']
                txt = f'{s.get("text", 0)} text, {s.get("button", 0)} buttons, {s.get("image", 0) + s.get("asset", 0)} images, {len(res["kit"]["system_colors"]) + len(res["kit"]["custom_colors"])} global colours'
                if res['links_todo']:
                    txt += f' · links to set: {", ".join(res["links_todo"][:6])}'
                if info:
                    self.link, self.edit = info['link'], info['edit']
                    txt = 'Page ready.  ' + txt
                self.res_lbl.configure(text=txt)
            self.run(fn, done)

        def act_site(self):
            if not self.manifest or not self.need('out', 'url', 'user', 'pw'):
                return
            wp = self.make_wp()
            out, pro, fonts = self.v['out'].get(), self.v_pro.get(), self.v['fonts'].get() or None
            if not messagebox.askyesno('Whole site', 'Badha desktop frames WordPress pages banse (Home = front page, Main Menu). Staging site che? Continue?'):
                return
            self.run(lambda log: do_make_site(out, wp, None, pro, fonts, log=log), lambda s: (self.say(s), self.res_lbl.configure(text=s.split('\n')[0])))

        def act_compare(self):
            fid = self.selected()
            if not fid or not self.link:
                messagebox.showinfo('Accuracy', 'Pela "Build + send to WordPress" karo.')
                return
            f = next(x for x in self.manifest['frames'] if x['id'] == fid)
            out, link = Path(self.v['out'].get()), self.link

            def fn(log):
                import compare
                return compare.compare(out / f['html'], link, out / '_compare', int(f['width']), log=log)
            self.run(fn, lambda r: self.res_lbl.configure(text=f'Pixel match vs exact HTML: {r["match"]}%  (diff: {r["files"]["diff"]})'))

    return App


def gui():
    make_app()().mainloop()


# ----------------------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(prog='studio', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd')
    sub.add_parser('gui')
    sub.add_parser('doctor')
    sub.add_parser('plugin-zip')
    sub.add_parser('mcp-config')
    p = sub.add_parser('import')
    p.add_argument('src')
    p.add_argument('-o', '--out', required=True)
    p.add_argument('--fonts')
    p = sub.add_parser('extract', help='EXTRACT ONLY: HTML/CSS + design JSON + assets (+ figma-extract.zip). No WordPress.')
    p.add_argument('src')
    p.add_argument('-o', '--out', required=True)
    p.add_argument('--fonts')
    p.add_argument('--pages', nargs='*')
    p.add_argument('--no-zip', action='store_true')
    p = sub.add_parser('site', help='FULL SITE on WordPress: every desktop frame -> page, Home front page, Main Menu')
    p.add_argument('out')
    p.add_argument('--frames', nargs='*')
    p.add_argument('--pro', action='store_true')
    p.add_argument('--fonts')
    p = sub.add_parser('make', help='ONE step: import + build + (push) + accuracy; prints a short summary')
    p.add_argument('src')
    p.add_argument('-o', '--out', required=True)
    p.add_argument('--frame')
    p.add_argument('--push', action='store_true')
    p.add_argument('--pro', action='store_true')
    p.add_argument('--responsive', action='store_true')
    p.add_argument('--fonts')
    p = sub.add_parser('build')
    p.add_argument('out')
    p.add_argument('frame')
    p.add_argument('--push', action='store_true')
    p.add_argument('--front-page', action='store_true')
    p.add_argument('--pro', action='store_true')
    p.add_argument('--fonts')
    sub.add_parser('mcp', help='run the MCP server (stdio) for Claude')
    a = ap.parse_args(argv)
    if a.cmd in (None, 'gui'):
        return gui()
    if a.cmd == 'mcp':
        import mcp_server
        return mcp_server.main()
    if a.cmd == 'doctor':
        wp = None
        if os.environ.get('FTE_WP_URL'):
            import wp_client
            wp = wp_client.WP()
        return 0 if doctor(print, wp) else 1
    if a.cmd == 'plugin-zip':
        print(make_plugin_zip())
    elif a.cmd == 'mcp-config':
        print(json.dumps(MCP_CONFIG, indent=2))
    elif a.cmd == 'import':
        do_import(a.src, a.out, a.fonts)
    elif a.cmd == 'extract':
        print(do_extract(a.src, a.out, a.fonts, a.pages, not a.no_zip))
    elif a.cmd == 'site':
        import wp_client
        print(do_make_site(a.out, wp_client.WP(), a.frames, a.pro, a.fonts))
    elif a.cmd == 'make':
        wp = None
        if a.push:
            import wp_client
            wp = wp_client.WP()
        print(do_make(a.src, a.out, a.frame, wp, a.fonts, True, a.pro, 'responsive' if a.responsive else 'exact'))
    elif a.cmd == 'build':
        wp = None
        if a.push:
            import wp_client
            wp = wp_client.WP()
        do_build(a.out, a.frame, wp, a.front_page, a.pro, a.fonts)


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()   # needed in the EXE: worker processes must not start a second app
    sys.exit(main())
