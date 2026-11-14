import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / 'ezmart_deco_optimizer' / 'tmp_readonly.py'
spec = importlib.util.spec_from_file_location('tmp_readonly', path)
tmp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tmp)

class TmpTests(unittest.TestCase):
    def test_no_action_opcode_accepted(self):
        for opcode in (0x4263, 0x4266, 0xc456):
            with self.assertRaises(tmp.ProtocolError):
                tmp.packet(opcode, b'', 0, 1)

    def test_fragmented_read_and_checksum_validation(self):
        body = b'{"result":{"network_quality":{"state":"idle","detail":[{"state":"optimal"}]}},"error_code":0}'
        import zlib
        def frame(chunk, offset):
            payload = struct.pack('>BBHBBHIII', 1, 2, tmp.QUALITY, 5, 0, 0,
                                  zlib.crc32(chunk), len(body), offset) + chunk
            head = struct.pack('>BBBBHHI', 1, 1, 5, 0, len(payload), 0, 1)
            return head + struct.pack('>I', zlib.crc32(head + bytes.fromhex('5a6b7c8d') + payload)) + payload
        class Channel:
            def __init__(self, data): self.data, self.sent = data, []
            def settimeout(self, value): pass
            def recv(self, n):
                n = min(n, 3)
                out, self.data = self.data[:n], self.data[n:]
                return out
            def sendall(self, data): self.sent.append(data)
        chan = Channel(frame(body[:10], 0) + frame(body[10:], 10))
        self.assertEqual(tmp.Reader(chan).request(tmp.QUALITY), body)
        self.assertEqual(len(chan.sent), 2)
        corrupted = bytearray(frame(body, 0)); corrupted[-1] ^= 1
        with self.assertRaises(tmp.ProtocolError):
            tmp.Reader(Channel(bytes(corrupted))).request(tmp.QUALITY)

    def test_secret_fields_and_unknown_state_dropped(self):
        result = tmp.project_quality({'result': {'network_quality': {'state': 'idle', 'token': 'secret',
            'detail': [{'state': 'optimal', 'password': 'secret'}, {'state': 'private-name'}]}}})
        self.assertEqual(result, {'state': 'idle', 'details': ['optimal'], 'fresh_scan_requested': False})

    def test_missing_pin_never_authenticates(self):
        calls = []
        class Transport:
            def __init__(self, sock): pass
            def start_client(self, **kw): pass
            def is_active(self): return True
            def get_remote_server_key(self): return SimpleNamespace(asbytes=lambda: b'key')
            def auth_password(self, *args, **kw): calls.append('auth')
            def close(self): pass
        fake = SimpleNamespace(Transport=Transport)
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {'paramiko': fake}), patch.object(tmp.socket, 'create_connection', return_value=SimpleNamespace(close=lambda: None)):
            result = tmp.run_probe({'host': '192.168.68.4', 'tmp_email': 'owner', 'tmp_password': 'secret'}, Path(directory), lambda *args: None)
            self.assertEqual(result['state'], 'host_key_required')
            self.assertEqual(calls, [])
            self.assertNotIn('secret', str(result))

    def test_cooldown_prevents_connection(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {'paramiko': SimpleNamespace()}), patch.object(tmp.socket, 'create_connection') as connect:
            data = Path(directory)
            (data/'tmp_state.json').write_text('{"next_login":1000}')
            self.assertEqual(tmp.run_probe({}, data, lambda *a: None, clock=lambda: 100)['state'], 'cooldown')
            connect.assert_not_called()

if __name__ == '__main__': unittest.main()
