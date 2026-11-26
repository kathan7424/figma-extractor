#!/usr/bin/env python3
"""Local test server for the isolated test WordPress: N PHP workers + a tiny threaded proxy on one port.

Why: PHP's built-in server is single-threaded, and WordPress/Elementor call their own site over HTTP while handling a request
(e.g. SVG upload) -> deadlock. With several workers behind a proxy that works. Test use only (not for production).

  python tools/test_server.py [--dir F:\\wp-fte-test] [--port 8088] [--workers 4]
"""
from __future__ import annotations

import argparse
import http.client
import itertools
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailers', 'transfer-encoding', 'upgrade'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default=r'F:\wp-fte-test')
    ap.add_argument('--port', type=int, default=8088)
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--php', default=r'C:\wamp64\bin\php\php8.3.14\php.exe')
    a = ap.parse_args()
    site = Path(a.dir)
    ports = [a.port + 10 + i for i in range(a.workers)]
    procs = [subprocess.Popen([a.php, '-d', 'xdebug.mode=off', '-d', 'display_errors=0', '-S', f'127.0.0.1:{p}', '-t', str(site), str(site / 'router.php')],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for p in ports]
    rr = itertools.cycle(ports)
    lock = threading.Lock()

    class H(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def handle_any(self):
            with lock:
                port = next(rr)
            body = self.rfile.read(int(self.headers.get('Content-Length') or 0)) if self.headers.get('Content-Length') else None
            hdrs = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
            try:
                c = http.client.HTTPConnection('127.0.0.1', port, timeout=300)
                c.request(self.command, self.path, body=body, headers=hdrs)
                r = c.getresponse()
                data = r.read()
                self.send_response(r.status)
                for k, v in r.getheaders():
                    if k.lower() not in HOP and k.lower() != 'content-length':
                        self.send_header(k, v)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(data)
            except Exception as e:  # worker not ready etc.
                self.send_error(502, str(e)[:100])

        do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = do_PATCH = do_OPTIONS = handle_any

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(('127.0.0.1', a.port), H)
    srv.daemon_threads = True
    print(f'test site on http://127.0.0.1:{a.port}  ({a.workers} PHP workers) - Ctrl+C to stop', flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            p.terminate()


if __name__ == '__main__':
    sys.exit(main())
