"""Tiny WordPress REST client (stdlib only). Auth = Application Password (HTTP Basic).

Credentials are NEVER written by this module. Pass them in memory, or via env FTE_WP_URL / FTE_WP_USER / FTE_WP_PASS.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class WPError(RuntimeError):
    pass


class WP:
    def __init__(self, url=None, user=None, password=None, timeout=120):
        self.url = (url or os.environ.get('FTE_WP_URL', '')).rstrip('/')
        user = user or os.environ.get('FTE_WP_USER', '')
        password = (password or os.environ.get('FTE_WP_PASS', '')).replace(' ', '')
        if not (self.url and user and password):
            raise WPError('WordPress URL, user ane Application Password joiye.')
        self._auth = 'Basic ' + base64.b64encode(f'{user}:{password}'.encode()).decode()
        self.timeout = timeout

    @classmethod
    def from_file(cls, path):
        """Test helper: file with 3 lines (url, user, app password)."""
        url, user, pw = Path(path).read_text('utf-8').split('\n')[:3]
        return cls(url, user, pw)

    def _req(self, method, route, body=None, headers=None, raw=False):
        url = f'{self.url}/index.php?rest_route=/{route.lstrip("/")}' if '?' not in route else f'{self.url}/{route.lstrip("/")}'
        data = None
        h = {'Authorization': self._auth, 'Accept': 'application/json', 'User-Agent': 'fte-studio'}
        if body is not None and not raw:
            data = json.dumps(body).encode('utf-8')
            h['Content-Type'] = 'application/json'
        elif raw:
            data = body
        h.update(headers or {})
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                txt = r.read().decode('utf-8', 'replace')
                return json.loads(txt) if txt.strip().startswith(('{', '[')) else txt
        except urllib.error.HTTPError as e:
            msg = e.read().decode('utf-8', 'replace')[:600]
            raise WPError(f'{method} {route} -> HTTP {e.code}: {msg}') from None
        except urllib.error.URLError as e:
            raise WPError(f'WordPress ma connect na thayu ({self.url}): {e.reason}') from None

    # ---- bridge
    def info(self):
        return self._req('GET', 'ftes/v1/info')

    def page(self, title, elementor_data, **kw):
        return self._req('POST', 'ftes/v1/page', dict(title=title, elementor_data=elementor_data, **kw))

    def template(self, title, type_, elementor_data, conditions=None):
        return self._req('POST', 'ftes/v1/template', dict(title=title, type=type_, elementor_data=elementor_data, conditions=conditions or []))

    def kit(self, settings=None):
        return self._req('POST', 'ftes/v1/kit', {'settings': settings}) if settings is not None else self._req('GET', 'ftes/v1/kit')

    def schema(self, post_types, field_groups):
        return self._req('POST', 'ftes/v1/schema', {'post_types': post_types, 'field_groups': field_groups})

    def posts(self, post_type, items):
        return self._req('POST', 'ftes/v1/posts', {'post_type': post_type, 'items': items})

    def menu(self, name, items, location=''):
        return self._req('POST', 'ftes/v1/menu', {'name': name, 'items': items, 'location': location})

    def settings(self, **kw):
        return self._req('POST', 'ftes/v1/settings', kw)

    def upload_fonts(self, fonts_dir, used, cache_file=None):
        """Send the font files that match the (family, style) pairs the design uses. Returns number of files sent."""
        import re
        weights = [('thin', 100), ('extralight', 200), ('light', 300), ('regular', 400), ('medium', 500), ('semibold', 600), ('extrabold', 800),
                   ('bold', 700), ('black', 900), ('heavy', 900)]
        norm = lambda s: re.sub(r'[^a-z0-9]', '', str(s).lower())
        files = [f for f in sorted(Path(fonts_dir).iterdir()) if f.suffix.lower() in ('.woff2', '.woff', '.ttf', '.otf')]
        cache = json.loads(Path(cache_file).read_text('utf-8')) if cache_file and Path(cache_file).exists() else {}
        sent = 0
        for fam, style in sorted(used):
            want = norm(fam) + norm(style)
            cands = [f for f in files if norm(f.stem).endswith(want)] or \
                    ([f for f in files if norm(f.stem).endswith(norm(fam))] if norm(style) == 'regular' else [])
            if not cands:
                continue
            f = cands[0]
            key = f'{self.url}|{f.name}|{f.stat().st_size}'
            if key in cache:
                continue
            ns = norm(style)
            weight = next((w for k, w in weights if k in ns), 400)
            self._req('POST', 'ftes/v1/fonts', {'family': fam, 'weight': str(weight), 'style': 'italic' if 'italic' in ns else 'normal',
                                                'ext': f.suffix.lower().lstrip('.'), 'data': base64.b64encode(f.read_bytes()).decode()})
            cache[key] = 1
            sent += 1
        if cache_file:
            Path(cache_file).write_text(json.dumps(cache), 'utf-8')
        return sent

    # ---- media (core REST)
    def upload_media(self, path, alt='', title=''):
        p = Path(path)
        mime = mimetypes.guess_type(p.name)[0] or 'application/octet-stream'
        if p.suffix.lower() == '.svg':
            mime = 'image/svg+xml'
        res = self._req('POST', 'wp/v2/media', p.read_bytes(), raw=True, headers={
            'Content-Type': mime, 'Content-Disposition': f'attachment; filename="{p.name}"'})
        if alt or title:
            self._req('POST', f'wp/v2/media/{res["id"]}', {'alt_text': alt, 'title': title or p.stem})
        return {'id': res['id'], 'url': res['source_url']}

    def render(self, link):
        """Fetch the public HTML of a page (for checks)."""
        req = urllib.request.Request(link, headers={'User-Agent': 'fte-studio'})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.read().decode('utf-8', 'replace')
