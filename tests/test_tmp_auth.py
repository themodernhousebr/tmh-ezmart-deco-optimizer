"""Auth contract tests with no socket traffic and no Paramiko installation."""
from test_tmp_readonly import tmp
from pathlib import Path
from types import SimpleNamespace
import hashlib
import tempfile
import unittest
from unittest.mock import patch

class AuthTests(unittest.TestCase):
    def run_fake(self, pin, failure=False, mode="pinned"):
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
        options = {'tmp_host_key_mode': mode, 'host': '192.168.68.4', 'tmp_host_key_sha256': pin,
                   'tmp_email': 'Owner@Example.com', 'tmp_password': 'SECRET'}
        if mode is None:
            options.pop('tmp_host_key_mode')
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

    def test_automatic_accepts_current_key_without_pin_or_with_old_pin(self):
        for pin, mode in (('', 'automatic'), ('0'*64, 'automatic'), ('0'*64, None)):
            result, calls, writes = self.run_fake(pin, mode=mode)
            self.assertEqual(result['stage'], 'channel')
            self.assertEqual(len(calls), 1)

    def test_invalid_mode_blocks_credentials(self):
        result, calls, writes = self.run_fake('', mode='unexpected')
        self.assertEqual(result['state'], 'invalid_host_key_mode')
        self.assertEqual(calls, [])

    def test_trust_mode_keeps_existing_journal_target(self):
        from m9_actions import target_hash
        options = {'host': '192.168.68.4', 'tmp_host_key_sha256': 'a'*64}
        self.assertEqual(target_hash(dict(options, tmp_host_key_mode='automatic')),
                         target_hash(dict(options, tmp_host_key_mode='pinned')))
        self.assertEqual(target_hash({'host':'192.168.68.4'}),
                         target_hash({'host':'192.168.68.4', 'tmp_host_key_mode':'automatic'}))
