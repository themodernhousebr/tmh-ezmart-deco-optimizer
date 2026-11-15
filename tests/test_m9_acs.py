"""Safety and protocol contracts for the M9's active ACS read operation."""
from test_tmp_readonly import tmp
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

APP = str(Path(__file__).resolve().parents[1] / 'ezmart_deco_optimizer')
if APP not in sys.path:
    sys.path.insert(0, APP)


def save(path, value):
    path.write_text(json.dumps(value))


def response(needed=True):
    return {'error_code': 0, 'result': {'need_optimize': needed,
            'optimize_item': {'channel': needed}, 'optimize_time': 20}}


class M9ScanTests(unittest.TestCase):
    def options(self, **extra):
        return {'tmp_action': 'scan', 'tmp_scan_backend': 'm9_acs',
                'tmp_scan_request_id': 'm9-001', 'tmp_scan_timeout_seconds': 120, **extra}

    def test_active_read_requires_its_own_gate_and_never_allows_apply(self):
        for kwargs in ({}, {'allow_scan': True}):
            with self.assertRaises(tmp.ProtocolError):
                tmp.packet(tmp.M9_ACS_SCAN, b'', 0, 1, **kwargs)
        tmp.packet(tmp.M9_ACS_SCAN, b'', 0, 1, allow_acs_scan=True)
        for code in (tmp.SCAN, 0x4250, 0x4266, 0xffff):
            with self.assertRaises(tmp.ProtocolError):
                tmp.packet(code, b'', 0, 1, allow_acs_scan=True)
        for code in (0x4250, 0x4266):
            with self.assertRaises(tmp.ProtocolError):
                tmp.packet(code, b'', 0, 1, allow_scan=True, allow_acs_scan=True)

    def test_single_request_is_journaled_before_dispatch_and_not_polled(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            calls = []
            class Reader:
                allow_acs_scan = False
                def request(self, code, timeout_seconds):
                    journal = json.loads((data / 'tmp_scan_journal.json').read_text())
                    assert journal['m9-001']['state'] == 'dispatch_uncertain'
                    assert self.allow_acs_scan
                    calls.append((code, timeout_seconds))
                    return json.dumps(response()).encode()
            reader = Reader()
            result = tmp.scan_once(reader, None, self.options(), data, save)
            self.assertEqual(calls, [(0x424f, 120)])
            self.assertEqual(result['state'], 'scan_response_received')
            self.assertTrue(result['need_optimize'])
            self.assertFalse(result['fresh_scan_confirmed'])
            self.assertTrue(result['result_may_be_cached'])
            self.assertFalse(result['apply_command_sent'])
            self.assertFalse(reader.allow_acs_scan)
            replay = tmp.scan_once(reader, None, self.options(), data, save)
            self.assertEqual(replay['state'], 'already_requested')
            self.assertEqual(len(calls), 1)

    def test_uncertain_timeout_is_not_replayed_even_if_backend_changes(self):
        class Reader:
            allow_acs_scan = False
            calls = 0
            def request(self, *args, **kwargs):
                self.calls += 1
                raise TimeoutError('private')
        with tempfile.TemporaryDirectory() as directory:
            data, reader = Path(directory), Reader()
            result = tmp.scan_once(reader, None, self.options(), data, save)
            self.assertEqual(result['state'], 'scan_timeout_uncertain')
            self.assertNotIn('private', str(result))
            replay = tmp.scan_once(reader, {'state': 'idle'}, self.options(tmp_scan_backend='legacy'), data, save)
            self.assertEqual(replay['state'], 'already_requested')
            self.assertEqual(reader.calls, 1)
            self.assertFalse(reader.allow_acs_scan)

    def test_unwritable_journal_prevents_dispatch(self):
        class Reader:
            def request(self, *args, **kwargs):
                raise AssertionError('Request sent before durable journal')
        def failed_save(*args):
            raise OSError('disk unavailable')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(OSError):
                tmp.scan_once(Reader(), None, self.options(), Path(directory), failed_save)

    def test_invalid_configuration_or_replay_never_opens_socket(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(tmp.socket, 'create_connection') as connect:
            data = Path(directory)
            cases = [self.options(tmp_scan_request_id=''), self.options(tmp_scan_timeout_seconds=0),
                     self.options(tmp_scan_backend='unknown')]
            for options in cases:
                tmp.run_probe(options, data, save)
            (data / 'tmp_scan_journal.json').write_text('{broken')
            self.assertEqual(tmp.run_probe(self.options(), data, save)['state'], 'invalid_scan_journal')
            save(data / 'tmp_scan_journal.json', {'m9-001': {'state': 'dispatch_uncertain'}})
            self.assertEqual(tmp.run_probe(self.options(), data, save)['state'], 'already_requested')
            connect.assert_not_called()

    def test_projection_drops_secrets_and_does_not_guess_success(self):
        doc = response(False)
        doc['token'] = 'secret'
        doc['result']['optimize_item']['mac'] = 'secret'
        doc['result']['password'] = 'secret'
        out = tmp.project_m9_acs(doc)
        self.assertFalse(out['need_optimize'])
        self.assertEqual(out['optimize_item'], {'channel': False})
        self.assertEqual(out['estimated_apply_seconds'], 20)
        self.assertNotIn('secret', str(out))
        malformed = [[], {}, {'error_code': True, 'result': doc['result']},
                     {'error_code': 0, 'result': {'need_optimize': 'false'}},
                     {'error_code': 0, 'result': {'need_optimize': True, 'optimize_item': {'channel': False}}}]
        for value in malformed:
            self.assertEqual(tmp.project_m9_acs(value)['state'], 'scan_response_unrecognized')
        self.assertEqual(tmp.project_m9_acs({'error_code': -4104, 'message': 'secret'}),
                         {'state': 'scan_rejected', 'api_code': -4104})

    def test_channel_snapshots_are_filtered_and_do_not_trigger_more_scans(self):
        class Reader:
            def request(self, *args, **kwargs):
                return json.dumps(response()).encode()
        calls = []
        def snapshot():
            calls.append(1)
            return {'band2_4': {'host': {'channel': 9 if len(calls) == 1 else 6,
                                         'ssid': 'secret', 'password': 'secret'}}}
        with tempfile.TemporaryDirectory() as directory:
            result = tmp.scan_once(Reader(), None, self.options(), Path(directory), save, channel_snapshot=snapshot)
            self.assertEqual(len(calls), 2)
            self.assertTrue(result['observed_channel_data_changed'])
            self.assertFalse(result['apply_command_sent'])
            self.assertNotIn('secret', str(result))

    def test_wire_request_has_empty_params_and_whole_response_deadline(self):
        body = json.dumps(response()).encode()
        def frame(chunk, offset):
            payload = struct.pack('>BBHBBHIII', 1, 2, tmp.M9_ACS_SCAN, 5, 0, 0,
                                  zlib.crc32(chunk), len(body), offset) + chunk
            head = struct.pack('>BBBBHHI', 1, 1, 5, 0, len(payload), 0, 1)
            return head + struct.pack('>I', zlib.crc32(head + bytes.fromhex('5a6b7c8d') + payload)) + payload
        class Channel:
            def __init__(self):
                self.data = frame(body[:10], 0) + frame(body[10:], 10)
                self.sent, self.timeouts = [], []
            def settimeout(self, timeout): self.timeouts.append(timeout)
            def sendall(self, value): self.sent.append(value)
            def recv(self, n):
                out, self.data = self.data[:n], self.data[n:]
                return out
        channel = Channel()
        reader = tmp.Reader(channel)
        reader.allow_acs_scan = True
        with patch.object(tmp.time, 'monotonic', return_value=100):
            self.assertEqual(reader.request(tmp.M9_ACS_SCAN, timeout_seconds=120), body)
        self.assertEqual(json.loads(channel.sent[0][36:])['params'], {})
        self.assertEqual(channel.timeouts, [120] * len(channel.timeouts))
        self.assertEqual(len(channel.sent), 2)
        self.assertEqual(channel.sent[0][20], 2)  # Request.
        self.assertEqual(channel.sent[1][20], 4)  # Fragment pull, not another request.

    def test_run_probe_m9_path_never_uses_legacy_quality_preflight(self):
        calls = []
        class Transport:
            def __init__(self, sock): pass
            def start_client(self, **kw): pass
            def is_active(self): return True
            def get_remote_server_key(self): return SimpleNamespace(asbytes=lambda: b'public-key')
            def auth_password(self, *args, **kw): pass
            def is_authenticated(self): return True
            def open_channel(self, *args, **kw):
                return SimpleNamespace(settimeout=lambda _: None, close=lambda: None)
            def close(self): pass
        class Reader:
            def __init__(self, channel): pass
            def request(self, code, **kw):
                calls.append(code)
                if code == tmp.ALLOC: return b'0' * 32
                if code == tmp.M9_ACS_SCAN: return json.dumps(response()).encode()
                if code == tmp.QUALITY:
                    return b'{"error_code":0,"result":{"network_quality":{"state":"idle","detail":[]}}}'
                raise AssertionError('Unexpected operation')
        pin = hashlib.sha256(b'public-key').hexdigest()
        options = self.options(host='192.168.68.4', tmp_host_key_sha256=pin,
                               tmp_email='owner@example.com', tmp_password='secret')
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {'paramiko': SimpleNamespace(Transport=Transport)}), patch.object(tmp.socket, 'create_connection', return_value=SimpleNamespace(close=lambda: None)), patch.object(tmp, 'Reader', Reader):
            data = Path(directory)
            self.assertEqual(tmp.run_probe(options, data, save)['state'], 'scan_response_received')
            self.assertEqual(calls, [tmp.ALLOC, tmp.M9_ACS_SCAN])
            calls.clear()
            options['tmp_action'] = 'read'
            result = tmp.run_probe(options, data, save)
            self.assertEqual(calls, [tmp.ALLOC, tmp.QUALITY])
            self.assertFalse(result['official_optimization_equivalent'])


if __name__ == '__main__':
    unittest.main()
