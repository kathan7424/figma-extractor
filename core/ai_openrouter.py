"""
ai_openrouter.py - OPTIONAL helper that uses OpenRouter's FREE models (":free"). Stdlib only.

AI is used only where it is safe: semantic naming/roles and reviewing a diff image. It NEVER changes geometry or colours
(those come from the Figma data, so accuracy does not depend on a model's guess).

Key: pass it in memory or set env OPENROUTER_API_KEY. It is never written to disk by this module.
Free models change often -> we ask OpenRouter's public /models list at runtime and fall back through several.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request

API = 'https://openrouter.ai/api/v1'


class AIError(RuntimeError):
    pass


def _http(method, url, body=None, key=None, timeout=90):
    h = {'Content-Type': 'application/json', 'User-Agent': 'fte-studio', 'X-Title': 'FTE Studio'}
    if key:
        h['Authorization'] = 'Bearer ' + key
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise AIError(f'HTTP {e.code}: {e.read().decode("utf-8", "replace")[:300]}') from None
    except urllib.error.URLError as e:
        raise AIError(f'network: {e.reason}') from None


def free_models(vision=False):
    """Public list (no key needed): free models, newest/biggest context first."""
    data = _http('GET', API + '/models')['data']
    out = []
    for m in data:
        p = m.get('pricing') or {}
        if str(p.get('prompt')) in ('0', '0.0') and str(p.get('completion')) in ('0', '0.0'):
            arch = m.get('architecture') or {}
            mods = arch.get('input_modalities') or []
            if 'text' not in (arch.get('output_modalities') or ['text']) or len(arch.get('output_modalities') or ['text']) > 1:
                continue  # text-only output (skips music/image generators)
            if vision and 'image' not in mods:
                continue
            if not vision and 'text' not in mods:
                continue
            out.append(m)
    out.sort(key=lambda m: -(m.get('context_length') or 0))
    return [m['id'] for m in out]


class OpenRouter:
    def __init__(self, key=None, log=print):
        self.key = key or os.environ.get('OPENROUTER_API_KEY', '')
        self.log = log

    @property
    def enabled(self):
        return bool(self.key)

    def chat(self, messages, vision=False, models=None, temperature=0.1, max_tokens=1500, tries=4):
        if not self.key:
            raise AIError('OpenRouter API key set nathi (optional).')
        models = list(models or free_models(vision))[: tries * 2]
        last = None
        for m in models[:tries]:
            try:
                res = _http('POST', API + '/chat/completions', {'model': m, 'messages': messages, 'temperature': temperature,
                                                                 'max_tokens': max_tokens}, key=self.key, timeout=120)
                txt = res['choices'][0]['message']['content']
                if txt:
                    return txt, m
            except (AIError, KeyError, IndexError) as e:
                last = e
                self.log(f'  AI model {m} failed: {str(e)[:80]}; try next')
                time.sleep(1)
        raise AIError(f'badha free models fail thaya: {last}')

    @staticmethod
    def parse_json(txt):
        m = re.search(r'\{[\s\S]*\}|\[[\s\S]*\]', txt)
        if not m:
            raise AIError('model e JSON na aapyu')
        return json.loads(m.group(0))

    # ---- safe uses -------------------------------------------------------------------------------------
    def name_sections(self, outline):
        """outline: list of {'index', 'text_samples', 'width', 'height'} -> {'index': {'role': 'header|hero|footer|section', 'name': 'Hero'}}"""
        prompt = ('You label website sections from a Figma design. For each item return a short semantic name and a role '
                  '(header, hero, footer, cta, section). Reply ONLY with JSON: {"items":[{"index":0,"name":"Hero","role":"hero"}]}.\n'
                  + json.dumps(outline)[:6000])
        txt, model = self.chat([{'role': 'user', 'content': prompt}])
        data = self.parse_json(txt)
        return {int(i['index']): i for i in data.get('items', []) if 'index' in i}, model

    def review_diff(self, reference_png, candidate_png, diff_png=None):
        """Vision review: where does the WordPress page differ from the design? (advice only, never auto-applied)"""
        def b64(p):
            return 'data:image/png;base64,' + base64.b64encode(open(p, 'rb').read()).decode()
        content = [{'type': 'text', 'text': 'Image 1 is the design, image 2 is the built web page. List the 5 most visible differences '
                                            '(layout, spacing, font, colour, missing/extra element) with approximate locations. Reply as JSON '
                                            '{"differences":[{"where":"...","issue":"...","fix":"..."}]}.'},
                   {'type': 'image_url', 'image_url': {'url': b64(reference_png)}},
                   {'type': 'image_url', 'image_url': {'url': b64(candidate_png)}}]
        txt, model = self.chat([{'role': 'user', 'content': content}], vision=True, max_tokens=900)
        return self.parse_json(txt), model
