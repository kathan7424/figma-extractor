"""Test the receiver with a hand-written WebSocket client that mimics the plugin UI."""
import base64, json, os, socket, struct, sys, time
from pathlib import Path
sys.path.insert(0, r'F:\figma-export-kit\desktop-tool')
import server

got = {}
def done(session, name): got['s'] = session; got['n'] = name
r = server.Receiver(Path(r'F:\figma-export-kit\_test\_inbox'), done, token='abc123', port=8799)
r.start()

def frame(op, payload):  # client frames must be masked
    mask = os.urandom(4)
    n = len(payload)
    head = bytes([0x80 | op])
    head += bytes([0x80 | n]) if n < 126 else (bytes([0x80 | 126]) + struct.pack('>H', n) if n < 65536 else bytes([0x80 | 127]) + struct.pack('>Q', n))
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload)) if n < 200000 else bytes(bytearray(payload))  # big: keep small below
    return head + mask + masked

def connect(token):
    s = socket.create_connection(('127.0.0.1', 8799))
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall(f'GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n'.encode())
    resp = s.recv(4096)
    assert b'101' in resp, resp
    s.sendall(frame(1, json.dumps({'type': 'hello', 'token': token}).encode()))
    return s

# wrong token is rejected
s = connect('nope'); time.sleep(0.3); print('bad token closed:', s.recv(10)[:1] == b'\x88')

s = connect('abc123')
def send(path, data):
    h = json.dumps({'path': path}).encode()
    s.sendall(frame(2, struct.pack('>I', len(h)) + h + data))
send('manifest.json', b'{"ok":1}')
send('assets/images/big.bin', os.urandom(100000))   # > 65535 -> 16-bit length path
s.sendall(frame(1, json.dumps({'type': 'done', 'name': 'T'}).encode()))
time.sleep(1.0)
ses = got.get('s')
print('done cb:', got.get('n'), '| files:', sorted(str(p.relative_to(ses)) for p in ses.rglob('*') if p.is_file()))
print('big size ok:', (ses / 'assets/images/big.bin').stat().st_size == 100000)
s2 = connect('abc123'); h = json.dumps({'path': '../evil.txt'}).encode()
s2.sendall(frame(2, struct.pack('>I', len(h)) + h + b'x')); time.sleep(0.5)
print('evil written outside:', (ses.parent / 'evil.txt').exists(), (ses.parent.parent / 'evil.txt').exists())
r.stop()
