"""Auth contract tests with no socket traffic and no Paramiko installation."""
from test_tmp_readonly import tmp
from pathlib import Path
from types import SimpleNamespace
import hashlib
import tempfile
import unittest
from unittest.mock import patch

class AuthTests(unittest.TestCase):
    def run_fake(self, pin, failure=False):
        calls, writes = [], []
        class Transport:
            def __init__(self, sock): pass
            def start_client(self, **kw): pass
            def is_active(self): return True
            def get_remote_server_key(self): return SimpleNamespace(asbytes=lambda: b'public-key')
            def auth_password(self, username, password, **kw):
                calls.append((username, password, kw))
                if failure: raise RuntimeError('SECRET')
            def is_authenticated(self): return True
            def open_channel(self, *args, **kwargs):
                raise RuntimeError('SECRET')
            def close(self): pass
        options = {'host': '192.168.68.4', 'tmp_host_key_sha256': pin,
                   'tmp_email': 'Owner@Example.com', 'tmp_password': 'SECRET'}
        with tempfile.TemporaryDirectory() as directory, patch.dict('sys.modules', {'paramiko': SimpleNamespace(Transport=Transport)}), patch.object(tmp.socket, 'create_connection', return_value=SimpleNamespace(close=lambda: None)):
            result = tmp.run_probe(options, Path(directory), lambda p, v: writes.append(v), clock=lambda: 100)
        self.assertNotIn('SECRET', str(result))
        return result, calls, writes

    def test_pin_mismatch_blocks_password(self):
        result, calls, writes = self.run_fake('0'*64)
        self.assertEqual(result['state'], 'host_key_mismatch')
        self.assertEqual(calls, [])
        self.assertEqual(writes, [])

    def test_single_password_attempt_and_failure_guard(self):
        pin = hashlib.sha256(b'public-key').hexdigest()
        result, calls, writes = self.run_fake(pin, failure=True)
        self.assertEqual(result, {'state': 'failed', 'stage': 'authentication'})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2], {'fallback': False})
        self.assertEqual(writes[-1], {'next_login': 1000})

    def test_success_clears_guard_before_channel(self):
        pin = hashlib.sha256(b'public-key').hexdigest()
        result, calls, writes = self.run_fake(pin)
        self.assertEqual(result['stage'], 'channel')
        self.assertEqual(len(calls), 1)
        self.assertEqual(writes, [{'next_login': 1000}, {'next_login': 0}])
