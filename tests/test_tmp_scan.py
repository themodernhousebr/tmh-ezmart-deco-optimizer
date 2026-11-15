from test_tmp_readonly import tmp
import json
from pathlib import Path
import tempfile
import unittest

class ScanTests(unittest.TestCase):
    def test_scan_gate_and_optimize_always_blocked(self):
        with self.assertRaises(tmp.ProtocolError): tmp.packet(tmp.SCAN, b'', 0, 1)
        tmp.packet(tmp.SCAN, b'', 0, 1, allow_scan=True)
        with self.assertRaises(tmp.ProtocolError): tmp.packet(0x4266, b'', 0, 1, allow_scan=True)

    def test_scan_once_and_replay_blocked(self):
        class Reader:
            allow_scan = False
            calls = []
            def request(self, code):
                self.calls.append(code)
                return json.dumps({'error_code': 0} if code == tmp.SCAN else {'result': {'network_quality': {'state': 'idle', 'detail': [{'state': 'optimal'}]}}}).encode()
        def save(path, value): path.write_text(json.dumps(value))
        with tempfile.TemporaryDirectory() as directory:
            reader = Reader(); data = Path(directory)
            options = {'tmp_scan_request_id': 'pilot-1'}
            result = tmp.scan_once(reader, {'state': 'idle'}, options, data, save, sleep=lambda _: None)
            self.assertEqual(result['state'], 'idle_after_ack')
            self.assertFalse(result['channels_applied'])
            self.assertEqual(tmp.scan_once(reader, {'state': 'idle'}, options, data, save)['state'], 'already_requested')
            self.assertEqual(reader.calls.count(tmp.SCAN), 1)

    def test_busy_does_not_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            result = tmp.scan_once(None, {'state': 'scanning'}, {'tmp_scan_request_id': 'pilot'}, Path(directory), lambda *a: None)
            self.assertEqual(result['state'], 'scan_blocked')
