"""Media preparation for Elementor: WebP conversion (skill 131), SEO file names + alt (skill 136), upload once (skill 140)."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path


def slug(s, n=48):
    s = re.sub(r'[^a-zA-Z0-9]+', '-', str(s or '')).strip('-').lower()[:n].strip('-')
    return s or 'image'


def prepare(export_dir, media, out_dir, max_side=2400, quality=84, log=print):
    """media: {key: {'ref': relative path, 'kind': 'image'|'svg', 'alt', 'name'}}  ->  {key: file path (WebP or SVG)}.
    Identical source files are converted once (de-duplicated by content hash)."""
    from PIL import Image
    export_dir, out_dir = Path(export_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_hash, result = {}, {}
    for key, m in media.items():
        src = export_dir / m['ref']
        if not src.exists():
            log(f'  ! missing media file {m["ref"]}')
            continue
        data = src.read_bytes()
        h = hashlib.sha1(data).hexdigest()[:10]
        if h in by_hash:
            result[key] = by_hash[h]
            continue
        base = f'{slug(m.get("name"))}-{h[:6]}'
        if src.suffix.lower() == '.svg':
            dest = out_dir / f'{base}.svg'
            dest.write_bytes(data)
        else:
            dest = out_dir / f'{base}.webp'
            with Image.open(src) as im:
                im.load()
                if max(im.size) > max_side:
                    r = max_side / max(im.size)
                    im = im.resize((round(im.width * r), round(im.height * r)), Image.LANCZOS)
                if im.mode not in ('RGB', 'RGBA'):
                    im = im.convert('RGBA' if 'transparency' in im.info or im.mode in ('LA', 'PA') else 'RGB')
                im.save(dest, 'WEBP', quality=quality, method=4)
        by_hash[h] = dest
        result[key] = dest
    return result


def resolve(elements, uploaded):
    """Replace FTE_MEDIA:<key> placeholders in an Elementor tree with {url,id} from `uploaded` {key: {'id','url'}}."""
    def fix(v):
        if isinstance(v, dict):
            u = v.get('url')
            if isinstance(u, str) and u.startswith('FTE_MEDIA:'):
                up = uploaded.get(u[len('FTE_MEDIA:'):])
                if up:
                    v['url'] = up['url']
                    v['id'] = up['id']
                return
            for x in v.values():
                fix(x)
        elif isinstance(v, list):
            for x in v:
                fix(x)
    fix(elements)
    return elements
