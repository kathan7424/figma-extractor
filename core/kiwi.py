"""Minimal Kiwi binary decoder (schema + message), pure Python stdlib. Used to read Figma .fig files offline.

Kiwi: https://github.com/evanw/kiwi  (binary schema format + varint encoding).
"""
from __future__ import annotations

import struct

NATIVE = ['bool', 'byte', 'int', 'uint', 'float', 'string', 'int64', 'uint64']
ENUM, STRUCT, MESSAGE = 0, 1, 2


class Reader:
    __slots__ = ('d', 'i')

    def __init__(self, data, pos=0):
        self.d = data
        self.i = pos

    def byte(self):
        v = self.d[self.i]
        self.i += 1
        return v

    def varuint(self):
        d, i, shift, v = self.d, self.i, 0, 0
        while True:
            b = d[i]
            i += 1
            v |= (b & 127) << shift
            if b < 128:
                break
            shift += 7
        self.i = i
        return v

    def varint(self):
        v = self.varuint()
        return ~(v >> 1) if v & 1 else v >> 1

    def varfloat(self):
        d, i = self.d, self.i
        if d[i] == 0:
            self.i = i + 1
            return 0.0
        bits = d[i] | d[i + 1] << 8 | d[i + 2] << 16 | d[i + 3] << 24
        self.i = i + 4
        bits = ((bits << 23) | (bits >> 9)) & 0xFFFFFFFF
        return struct.unpack('<f', struct.pack('<I', bits))[0]

    def string(self):
        d, i = self.d, self.i
        j = d.index(0, i)
        self.i = j + 1
        return d[i:j].decode('utf-8', 'replace')


def parse_schema(data: bytes):
    r = Reader(data)
    defs = []
    for _ in range(r.varuint()):
        name = r.string()
        kind = r.byte()
        fields = []
        for _ in range(r.varuint()):
            fname = r.string()
            t = r.varint()
            is_array = bool(r.byte())
            value = r.varuint()
            fields.append({'name': fname, 'type': t, 'array': is_array, 'value': value})
        defs.append({'name': name, 'kind': kind, 'fields': fields})
    return defs


class Decoder:
    def __init__(self, defs):
        self.defs = defs
        self.by_name = {d['name']: i for i, d in enumerate(defs)}
        self.readers = {}

    def type_reader(self, t):
        if t < 0:
            return {
                'bool': lambda r: bool(r.byte()), 'byte': lambda r: r.byte(), 'int': lambda r: r.varint(),
                'uint': lambda r: r.varuint(), 'float': lambda r: r.varfloat(), 'string': lambda r: r.string(),
                'int64': lambda r: r.varint(), 'uint64': lambda r: r.varuint(),
            }[NATIVE[~t]]
        return self.def_reader(t)

    def def_reader(self, idx):
        if idx in self.readers:
            return lambda r, idx=idx: self.readers[idx](r)
        d = self.defs[idx]
        holder = {}
        self.readers[idx] = lambda r: holder['f'](r)  # allows recursive types
        if d['kind'] == ENUM:
            names = {f['value']: f['name'] for f in d['fields']}

            def read_enum(r, names=names):
                v = r.varuint()
                return names.get(v, v)
            holder['f'] = read_enum
        elif d['kind'] == STRUCT:
            fs = []
            for f in d['fields']:
                fs.append((f['name'], f['array'], f['type']))
            compiled = [(n, a, None) for n, a, _ in fs]
            holder['types'] = [t for _, _, t in fs]

            def read_struct(r, fs=fs, holder=holder):
                if 'rd' not in holder:
                    holder['rd'] = [self.type_reader(t) for _, _, t in fs]
                out = {}
                for (n, arr, _), rd in zip(fs, holder['rd']):
                    if arr:
                        out[n] = [rd(r) for _ in range(r.varuint())]
                    else:
                        out[n] = rd(r)
                return out
            holder['f'] = read_struct
        else:
            by_id = {f['value']: f for f in d['fields']}

            def read_msg(r, by_id=by_id, holder=holder):
                if 'rd' not in holder:
                    holder['rd'] = {}
                rds = holder['rd']
                out = {}
                while True:
                    fid = r.varuint()
                    if fid == 0:
                        return out
                    f = by_id.get(fid)
                    if f is None:
                        raise ValueError(f'unknown field id {fid} in {d["name"]}')
                    rd = rds.get(fid)
                    if rd is None:
                        rd = rds[fid] = self.type_reader(f['type'])
                    if f['array']:
                        n = r.varuint()
                        if f['type'] == -2:  # byte[] fast path
                            out[f['name']] = bytes(r.d[r.i:r.i + n])
                            r.i += n
                        else:
                            out[f['name']] = [rd(r) for _ in range(n)]
                    else:
                        out[f['name']] = rd(r)
            holder['f'] = read_msg
        return self.readers[idx]

    def decode(self, root_name, data: bytes):
        return self.def_reader(self.by_name[root_name])(Reader(data))
