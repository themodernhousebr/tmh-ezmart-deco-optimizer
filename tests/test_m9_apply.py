"""No-network tests for application, interruption, replay and passive verification."""
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ezmart_deco_optimizer'))
import m9_actions as actions
from test_tmp_readonly import tmp


def save(path, value):
    path.write_text(json.dumps(value))


def scan_result(needed=True):
    return {'error_code': 0, 'result': {'need_optimize': needed,
            'optimize_item': {'channel': needed}, 'optimize_time': 20}}


def wlan(channel=9, secret='PRIVATE'):
    return {'password': secret, 'band2_4': {'host': {'channel': channel, 'ssid': secret, 'password': secret}},
            'band5_1': {'host': {'channel': 36}}, 'band5_2': {'host': {'channel': 149}}}


class FakeTime:
    def __init__(self): self.value = 10000
    def now(self): return self.value
    def wait(self, seconds): self.value += seconds; return False


class Reader:
    def __init__(self, scan=None, ack=None, error=None, hook=None):
        self.scan = scan_result() if scan is None else scan
        self.ack = {'error_code': 0, 'result': True} if ack is None else ack
        self.error, self.hook, self.calls = error, hook, []
        self.allow_acs_scan = self.allow_acs_apply = False
    def request(self, code, **kwargs):
        self.calls.append(code)
        if self.hook: self.hook(code)
        if code == actions.SCAN:
            assert self.allow_acs_scan and not self.allow_acs_apply
            return json.dumps(self.scan).encode()
        assert code == actions.APPLY and self.allow_acs_apply and not self.allow_acs_scan
        if self.error: raise self.error
        return json.dumps(self.ack).encode()


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data = Path(self.directory.name)
        self.timer = FakeTime()
        self.options = {'host': '192.168.68.4', 'tmp_host_key_sha256': 'a'*64,
                        'tmp_scan_backend': 'm9_acs', 'tmp_optimize_request_id': 'apply-001',
                        'tmp_action': 'optimize'}
        self.snapshot_calls = 0
    def changed(self):
        self.snapshot_calls += 1
        return wlan(9 if self.snapshot_calls == 1 else 6)
    def run_action(self, reader, **kwargs):
        return actions.optimize_once(reader, self.options, self.data, kwargs.pop('save', save),
                                     kwargs.pop('snapshot', self.changed), clock=self.timer.now,
                                     monotonic=self.timer.now, wait=self.timer.wait, **kwargs)
    def verify(self, **kwargs):
        return actions.verify_only(self.options, self.data, save, kwargs.pop('snapshot', lambda: wlan(6)),
                                   clock=self.timer.now, monotonic=self.timer.now, wait=self.timer.wait, **kwargs)

    def test_positive_recommendation_then_single_application_and_stable_channels(self):
        def inspect(code):
            stored = json.loads((self.data/'tmp_optimize_journal.json').read_text())['actions']['apply-001']['report']
            if code == actions.APPLY:
                self.assertEqual(stored['state'], 'apply_dispatch_uncertain')
                self.assertTrue(stored['apply_command_attempted'])
                self.assertEqual(stored['preflight']['need_optimize'], True)
        reader = Reader(hook=inspect)
        report = self.run_action(reader)
        self.assertEqual(reader.calls, [0x424f, 0x4250])
        self.assertEqual(report['state'], 'apply_acknowledged')
        self.assertEqual(report['conclusion'], 'acknowledged_with_channel_change')
        self.assertEqual(report['verification']['stable_samples'], 3)
        self.assertEqual(report['verification']['channel_changes'], [{'band': 'band2_4', 'before': 9, 'after': 6}])
        self.assertFalse(reader.allow_acs_apply)
        self.assertFalse(reader.allow_acs_scan)
        for p in self.data.glob('*.json'):
            self.assertNotIn('PRIVATE', p.read_text())
            self.assertNotIn('a'*64, p.read_text())

    def test_negative_recommendation_does_not_apply_or_observe(self):
        reader = Reader(scan=scan_result(False))
        report = self.run_action(reader)
        self.assertEqual(reader.calls, [actions.SCAN])
        self.assertEqual(report['state'], 'optimization_not_needed')
        self.assertFalse(report['apply_command_attempted'])
        self.assertEqual(self.snapshot_calls, 0)
        self.assertEqual(self.verify()['state'], 'nothing_to_verify')

    def test_malformed_or_rejected_preflight_never_applies(self):
        for index, document in enumerate(({}, {'error_code': -4104}, scan_result('true'))):
            self.options['tmp_optimize_request_id'] = 'bad-'+str(index)
            reader = Reader(scan=document)
            report = self.run_action(reader)
            self.assertEqual(report['state'], 'preflight_failed')
            self.assertEqual(reader.calls, [actions.SCAN])

    def test_missing_baseline_blocks_application(self):
        reader = Reader()
        report = self.run_action(reader, snapshot=lambda: {'password': 'PRIVATE'})
        self.assertEqual(report['state'], 'baseline_unavailable')
        self.assertEqual(reader.calls, [actions.SCAN])

    def test_replay_does_not_rescan_reapply_or_reconnect(self):
        reader = Reader()
        self.run_action(reader)
        with patch.object(tmp.socket, 'create_connection') as connect:
            replay = tmp.run_probe(self.options, self.data, save, clock=self.timer.now)
            self.assertEqual(replay['state'], 'already_requested')
            connect.assert_not_called()
        self.assertEqual(self.run_action(reader)['state'], 'already_requested')
        self.assertEqual(reader.calls, [actions.SCAN, actions.APPLY])

    def test_different_id_cannot_bypass_cooldown(self):
        self.run_action(Reader())
        self.options['tmp_optimize_request_id'] = 'apply-002'
        reader = Reader()
        self.assertEqual(self.run_action(reader)['state'], 'apply_cooldown')
        self.assertEqual(reader.calls, [])
        self.timer.value += 900
        self.assertIsNone(actions.prepare(self.options, self.data, self.timer.now())[1])

    def test_lost_ack_is_not_success_even_with_observed_change(self):
        reader = Reader(error=TimeoutError('PRIVATE'))
        report = self.run_action(reader)
        self.assertEqual(reader.calls.count(actions.APPLY), 1)
        self.assertEqual(report['state'], 'apply_outcome_uncertain')
        self.assertEqual(report['conclusion'], 'unconfirmed_with_channel_change')
        self.assertNotIn('PRIVATE', str(report))
        self.assertEqual(self.run_action(reader)['state'], 'already_requested')

    def test_unknown_ack_and_rejection_are_preserved(self):
        for index, (ack, state) in enumerate((({'error_code': True, 'token': 'PRIVATE'}, 'apply_outcome_uncertain'),
                                              ({'error_code': 1, 'message': 'PRIVATE'}, 'apply_rejected'))):
            self.options['tmp_optimize_request_id'] = 'ack-'+str(index)
            reader = Reader(ack=ack)
            self.timer.value += 901
            report = self.run_action(reader)
            self.assertEqual(report['state'], state)
            self.assertNotIn('PRIVATE', str(report))
            self.assertEqual(reader.calls.count(actions.APPLY), 1)

    def test_accepted_without_channel_change_is_not_claimed_as_improvement(self):
        report = self.run_action(Reader(), snapshot=lambda: wlan(9))
        self.assertEqual(report['conclusion'], 'acknowledged_without_observed_change')
        self.assertFalse(report['verification']['observed_channels_changed'])

    def test_crash_after_dispatch_is_durable_and_new_ids_blocked(self):
        reader = Reader(error=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt): self.run_action(reader)
        self.assertFalse(reader.allow_acs_apply)
        self.assertEqual(self.run_action(Reader())['state'], 'already_requested')
        self.options['tmp_optimize_request_id'] = 'apply-002'
        self.timer.value += 10000
        self.assertEqual(self.run_action(Reader())['state'], 'previous_apply_unverified')
        self.options['tmp_optimize_request_id'] = 'apply-001'
        report = self.verify()['report']
        self.assertEqual(report['state'], 'apply_dispatch_uncertain')
        self.assertEqual(report['verification']['state'], 'stable')
        self.assertEqual(report['conclusion'], 'unconfirmed_with_channel_change')

    def test_journal_failure_before_apply_prevents_send(self):
        def failed_save(path, value):
            if path.name == 'tmp_optimize_journal.json':
                report = value['actions']['apply-001']['report']
                if report['state'] == 'apply_dispatch_uncertain': raise OSError('PRIVATE')
            save(path, value)
        reader = Reader()
        result = self.run_action(reader, save=failed_save)
        self.assertEqual(result['state'], 'action_storage_or_state_error')
        self.assertEqual(reader.calls, [actions.SCAN])
        self.assertNotIn('PRIVATE', str(result))

    def test_verify_only_does_not_open_tmp_or_send_any_command(self):
        self.run_action(Reader(error=ConnectionError('PRIVATE')))
        self.options['tmp_action'] = 'verify'
        with patch.object(tmp.socket, 'create_connection') as connect:
            result = tmp.run_probe(self.options, self.data, save, clock=self.timer.now,
                                   channel_snapshot=lambda: wlan(6), wait=self.timer.wait)
            self.assertEqual(result['state'], 'verification_only')
            self.assertEqual(result['report']['verification']['state'], 'stable')
            connect.assert_not_called()

    def test_different_target_cannot_verify_or_replay_an_action(self):
        self.run_action(Reader())
        self.options['host'] = '192.168.68.5'
        self.assertEqual(self.verify()['state'], 'target_mismatch')
        self.assertEqual(self.run_action(Reader())['state'], 'target_mismatch')

    def test_legacy_backend_cannot_apply(self):
        self.options['tmp_scan_backend'] = 'legacy'
        reader = Reader()
        self.assertEqual(self.run_action(reader)['state'], 'invalid_optimize_configuration')
        self.assertEqual(reader.calls, [])

    def test_bad_settings_and_corrupt_journal_fail_closed(self):
        for key, value in (('tmp_verify_interval_seconds', 61), ('tmp_apply_timeout_seconds', True),
                           ('tmp_scan_timeout_seconds', 1000)):
            opts = dict(self.options, **{key: value})
            self.assertEqual(actions.prepare(opts, self.data, self.timer.now())[1]['state'], 'invalid_optimize_configuration')
        (self.data/'tmp_optimize_journal.json').write_text('{broken')
        self.assertEqual(self.run_action(Reader())['state'], 'invalid_optimize_journal')

    def test_malformed_persisted_verification_or_baseline_blocks_all_actions(self):
        self.run_action(Reader())
        path = self.data / 'tmp_optimize_journal.json'
        original = path.read_text()
        for field, value in (('verification', None), ('apply_ack', []), ('channels_before_apply', {})):
            journal = json.loads(original)
            journal['actions']['apply-001']['report'][field] = value
            save(path, journal)
            reader = Reader()
            self.assertEqual(self.run_action(reader)['state'], 'invalid_optimize_journal')
            self.assertEqual(self.verify()['state'], 'invalid_optimize_journal')
            self.assertEqual(reader.calls, [])

    def test_stopping_after_scan_prevents_apply(self):
        cancelled = [False]
        reader = Reader(hook=lambda code: cancelled.__setitem__(0, True))
        report = self.run_action(reader, should_stop=lambda: cancelled[0])
        self.assertEqual(report['state'], 'cancelled_before_apply')
        self.assertEqual(reader.calls, [actions.SCAN])

    def test_interrupted_verification_can_resume_without_reapplication(self):
        stop = [False]
        reader = Reader(hook=lambda code: stop.__setitem__(0, code == actions.APPLY))
        report = self.run_action(reader, should_stop=lambda: stop[0])
        self.assertEqual(report['verification']['state'], 'interrupted')
        verified = self.verify()
        self.assertEqual(verified['report']['verification']['state'], 'stable')
        self.assertEqual(reader.calls.count(actions.APPLY), 1)

    def test_missing_bands_never_count_as_stable_verification(self):
        calls = [0]
        def snapshot():
            calls[0] += 1
            return wlan() if calls[0] == 1 else {'band2_4': {'host': {'channel': 6}}}
        report = self.run_action(Reader(), snapshot=snapshot)
        self.assertEqual(report['verification']['state'], 'timeout')
        self.assertEqual(report['conclusion'], 'verification_incomplete')
        self.options['tmp_optimize_request_id'] = 'apply-002'
        self.assertEqual(self.run_action(Reader())['state'], 'previous_apply_unverified')

    def test_lock_blocks_concurrent_action(self):
        reader = Reader()
        with actions.action_lock(self.data):
            self.assertEqual(self.run_action(reader)['state'], 'action_in_progress')
        self.assertEqual(reader.calls, [])

    def test_apply_packet_needs_separate_gate_and_legacy_write_never_allowed(self):
        with self.assertRaises(tmp.ProtocolError): tmp.packet(actions.APPLY, b'', 0, 1, allow_acs_scan=True)
        tmp.packet(actions.APPLY, b'', 0, 1, allow_acs_apply=True)
        for code in (actions.SCAN, tmp.SCAN, 0x4266, 0xffff):
            with self.assertRaises(tmp.ProtocolError): tmp.packet(code, b'', 0, 1, allow_acs_apply=True)

    def test_apply_wire_request_empty_params_and_fragment_fetch_does_not_replay(self):
        body = b'{"error_code":0,"result":true}'
        def frame(chunk, offset):
            payload = struct.pack('>BBHBBHIII', 1, 2, actions.APPLY, 5, 0, 0,
                                  zlib.crc32(chunk), len(body), offset) + chunk
            header = struct.pack('>BBBBHHI', 1, 1, 5, 0, len(payload), 0, 1)
            return header + struct.pack('>I', zlib.crc32(header + bytes.fromhex('5a6b7c8d') + payload)) + payload
        class Channel:
            def __init__(self):
                self.data = frame(body[:10], 0) + frame(body[10:], 10)
                self.sent, self.timeouts = [], []
            def settimeout(self, seconds): self.timeouts.append(seconds)
            def sendall(self, value): self.sent.append(value)
            def recv(self, size):
                out, self.data = self.data[:size], self.data[size:]
                return out
        channel = Channel()
        reader = tmp.Reader(channel)
        reader.allow_acs_apply = True
        with patch.object(tmp.time, 'monotonic', return_value=100):
            self.assertEqual(reader.request(actions.APPLY, timeout_seconds=60), body)
        self.assertEqual(json.loads(channel.sent[0][36:])['params'], {})
        self.assertEqual([frame[20] for frame in channel.sent], [2, 4])
        self.assertEqual(channel.timeouts, [60] * len(channel.timeouts))

    def test_full_probe_routes_only_allocate_scan_apply_and_closes_resources(self):
        reader = Reader()
        calls, closed = [], []
        class Transport:
            def __init__(self, sock): pass
            def start_client(self, **kwargs): pass
            def is_active(self): return True
            def get_remote_server_key(self): return SimpleNamespace(asbytes=lambda: b'public-key')
            def auth_password(self, *args, **kwargs): pass
            def is_authenticated(self): return True
            def open_channel(self, *args, **kwargs):
                return SimpleNamespace(settimeout=lambda _: None, close=lambda: closed.append('channel'))
            def close(self): closed.append('transport')
        original_request = reader.request
        def request(code, **kwargs):
            calls.append(code)
            return b'0' * 32 if code == tmp.ALLOC else original_request(code, **kwargs)
        reader.request = request
        self.options.update(tmp_host_key_sha256=hashlib.sha256(b'public-key').hexdigest(),
                            tmp_email='owner@example.com', tmp_password='PRIVATE')
        with patch.dict(sys.modules, {'paramiko': SimpleNamespace(Transport=Transport)}), \
             patch.object(tmp.socket, 'create_connection', return_value=SimpleNamespace(close=lambda: closed.append('socket'))), \
             patch.object(tmp, 'Reader', return_value=reader):
            result = tmp.run_probe(self.options, self.data, save, clock=self.timer.now,
                                   channel_snapshot=self.changed, wait=self.timer.wait)
        self.assertEqual(result['conclusion'], 'acknowledged_with_channel_change')
        self.assertEqual(calls, [tmp.ALLOC, actions.SCAN, actions.APPLY])
        self.assertEqual(closed, ['channel', 'transport', 'socket'])


if __name__ == '__main__': unittest.main()
