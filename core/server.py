#!/usr/bin/env python3
"""
server.py - receives a Figma export LIVE from the plugin (no zip) over a local WebSocket.

stdlib only. Binds to 127.0.0.1:8787 (this computer only) and requires the token shown here.
Protocol (plugin -> app):
  text   {"type":"hello","token":"..."}
  binary [4-byte big-endian header length][header JSON {"path": "pages/Home/x.json"}][file bytes]   (repeat)
  text   {"type":"done","name":"<file name>"}

CLI:  python server.py --out F:\\exports\\my-site [--autofix]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import socket
import struct
import sys
import threading
import time
from pathlib import Path

GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
MAX_MESSAGE = 512 * 1024 * 1024
PORT = 8787


def safe_rel(path: str) -> Path:
    p = Path(path.replace('\\', '/'))
    if p.is_absolute() or '..' in p.parts or not p.parts or ':' in path:
        raise ValueError(f'unsafe path: {path!r}')
    return p


def recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(65536, n - len(buf)))
        if not chunk:
            raise ConnectionError('closed')
        buf += chunk
    return bytes(buf)


def read_message(sock):
    """Returns (opcode, payload) for one complete WebSocket message (handles fragments, ping/pong)."""
    data, first_op = bytearray(), None
    while True:
        b0, b1 = recv_exact(sock, 2)
        fin, op = b0 & 0x80, b0 & 0x0F
        ln = b1 & 0x7F
        if ln == 126:
            ln = struct.unpack('>H', recv_exact(sock, 2))[0]
        elif ln == 127:
            ln = struct.unpack('>Q', recv_exact(sock, 8))[0]
        if ln > MAX_MESSAGE:
            raise ValueError('message too large')
        mask = recv_exact(sock, 4) if b1 & 0x80 else None
        payload = bytearray(recv_exact(sock, ln)) if ln else bytearray()
        if mask:
            # fast unmask using int XOR on the whole buffer
            m = (mask * (ln // 4 + 1))[:ln]
            payload = bytearray((int.from_bytes(payload, 'big') ^ int.from_bytes(m, 'big')).to_bytes(ln, 'big')) if ln else payload
        if op == 0x9:  # ping
            send_frame(sock, 0xA, bytes(payload))
            continue
        if op == 0xA:
            continue
        if op == 0x8:
            return 0x8, b''
        if op != 0x0:
            first_op = op
        data += payload
        if fin:
            return first_op, bytes(data)


def send_frame(sock, op, payload=b''):
    head = bytes([0x80 | op])
    n = len(payload)
    head += bytes([n]) if n < 126 else (b'\x7e' + struct.pack('>H', n) if n < 65536 else b'\x7f' + struct.pack('>Q', n))
    sock.sendall(head + payload)


class Receiver:
    def __init__(self, inbox: Path, on_done, log=print, token=None, port=PORT):
        self.inbox, self.on_done, self.log = Path(inbox), on_done, log
        self.token = token or secrets.token_hex(4)
        self.port = port
        self.sock = None
        self.stop_flag = threading.Event()

    def start(self):
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('127.0.0.1', self.port))
        s.listen(4)
        s.settimeout(0.5)
        self.sock = s
        threading.Thread(target=self.loop, daemon=True).start()
        self.log(f'Live receive ready: ws://localhost:{self.port}   token: {self.token}')

    def stop(self):
        self.stop_flag.set()
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass

    def loop(self):
        while not self.stop_flag.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()

    def handshake(self, conn):
        req = b''
        while b'\r\n\r\n' not in req:
            chunk = conn.recv(4096)
            if not chunk or len(req) > 16384:
                return False
            req += chunk
        headers = {}
        for line in req.decode('latin-1').split('\r\n')[1:]:
            if ':' in line:
                k, v = line.split(':', 1)
                headers[k.strip().lower()] = v.strip()
        key = headers.get('sec-websocket-key')
        if not key:
            conn.sendall(b'HTTP/1.1 400 Bad Request\r\n\r\n')
            return False
        acc = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        conn.sendall(('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                      f'Sec-WebSocket-Accept: {acc}\r\n\r\n').encode())
        return True

    def handle(self, conn):
        try:
            conn.settimeout(120)
            if not self.handshake(conn):
                return
            op, payload = read_message(conn)
            hello = json.loads(payload.decode('utf-8')) if op == 1 else {}
            if hello.get('type') != 'hello' or not secrets.compare_digest(str(hello.get('token', '')), self.token):
                self.log('Rejected connection: wrong token')
                send_frame(conn, 0x8, b'\x03\xe8')
                return
            session = self.inbox / time.strftime('%Y%m%d-%H%M%S')
            session.mkdir(parents=True, exist_ok=True)
            count, size = 0, 0
            while True:
                op, payload = read_message(conn)
                if op == 0x8:
                    return
                if op == 2:
                    hl = struct.unpack('>I', payload[:4])[0]
                    head = json.loads(payload[4:4 + hl].decode('utf-8'))
                    dest = session / safe_rel(head['path'])
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(payload[4 + hl:])
                    count += 1
                    size += len(payload)
                    if count % 50 == 0:
                        self.log(f'  received {count} files ({size / 1048576:.1f} MB)')
                elif op == 1:
                    msg = json.loads(payload.decode('utf-8'))
                    if msg.get('type') == 'done':
                        self.log(f'Received {count} files ({size / 1048576:.1f} MB)')
                        send_frame(conn, 0x8, b'\x03\xe8')
                        threading.Thread(target=self.on_done, args=(session, msg.get('name', 'figma-export')), daemon=True).start()
                        return
        except Exception as e:  # keep the server alive for the next attempt
            self.log(f'Receive error: {e}')
        finally:
            try:
                conn.close()
            except OSError:
                pass


def main(argv=None):
    ap = argparse.ArgumentParser(description='Receive Figma export live from the plugin and convert it')
    ap.add_argument('--out', required=True, help='output folder')
    ap.add_argument('--autofix', action='store_true')
    ap.add_argument('--mode', choices=['exact', 'responsive'], default='exact')
    a = ap.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import figma2html

    def done(session, name):
        figma2html.convert(session, a.out, mode=a.mode)
        import verify
        verify.verify(a.out, autofix=a.autofix)

    r = Receiver(Path(a.out).parent / '_inbox', done)
    r.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        r.stop()


if __name__ == '__main__':
    main()
