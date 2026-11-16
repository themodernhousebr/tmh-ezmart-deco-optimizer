"""X50 guest contract: pinned TPAP, complete preflight and one preserving write."""
import copy
import json
import struct
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_guest_wifi as fixture
wlan = fixture.wlan
import guest_wifi as guest
import x50_backend as backend
import x50_tpap as protocol
import run
from ha_control import Controller


class X50GuestTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixture.GuestTests('test_existing_desired_state_skips_write')
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        f = self.fx
        f.options.update(deco_model='x50', x50_certificate_sha256='b'*64,
                         tmp_email='owner@example.test', tmp_password='PRIVATE', poll_seconds=300)
        f.profile['device_list'][0].update(device_model='X50', hardware_ver='1.30',
                         software_ver='1.8.4 Build 26051413 Rel. 27460')
        f.mode = {'mode': 'Router'}
        f.controller = Controller(f.options, f.data, run.save, clock=lambda: f.now)
        f.monitor()

    def test_enable_disable_and_privacy(self):
        f = self.fx
        for action, expected in [('guest_on', True), ('guest_off', False)]:
            result = f.execute(action)
            self.assertEqual(result['state'], 'guest_enabled_verified' if expected else 'guest_disabled_verified')
            self.assertEqual(f.write_calls[-1], {band: {'guest': {'enable': expected, 'host_isolation': False}}
                                               for band in guest.BANDS})
            self.assertNotIn('PRIVATE', json.dumps(result))
            self.assertEqual(f.raw['band2_4']['host'], wlan()['band2_4']['host'])
            f.now += 61
            f.monitor(raw=f.raw)
        self.assertEqual(len(f.write_calls), 2)

    def test_unsupported_profile_never_writes(self):
        f = self.fx
        for field, value in [('device_model', 'M9Plus'), ('hardware_ver', '2.0'),
                             ('software_ver', 'other')]:
            saved = f.profile['device_list'][0][field]
            f.profile['device_list'][0][field] = value
            self.assertFalse(guest.supported_profile(f.reader, 'x50'))
            f.profile['device_list'][0][field] = saved
        f.mode = {'mode': 'AP'}
        self.assertEqual(f.execute()['state'], 'guest_profile_not_validated')
        self.assertEqual(f.write_calls, [])

    def test_lost_ack_never_replays(self):
        self.fx.lost_ack = True
        self.assertEqual(self.fx.execute()['state'], 'guest_state_confirmed_without_ack')
        self.assertEqual(len(self.fx.write_calls), 1)

    def test_timeout_without_change_never_claims_success(self):
        self.fx.change = False
        self.assertEqual(self.fx.execute()['state'], 'guest_outcome_uncertain')
        self.assertEqual(len(self.fx.write_calls), 1)

    def test_partial_configuration_blocks_write(self):
        self.fx.raw['band5_1']['guest'].pop('host_isolation')
        self.assertEqual(self.fx.execute()['state'], 'guest_configuration_unavailable')
        self.assertEqual(self.fx.write_calls, [])

    def test_x50_auth_cooldown_is_used(self):
        f = self.fx
        run.save(f.data/'x50_auth_state.json', {'next_login': f.now + 900})
        self.assertEqual(f.controller.accept('guest_on'), 'tmp_auth_cooldown')
        self.assertIsNone(f.controller.take())

    def test_guest_target_uses_x50_certificate_not_m9_pin(self):
        f = self.fx
        self.assertEqual(guest.target_hash(f.options), backend.target_hash(f.options))
        changed = dict(f.options, tmp_host_key_sha256='c'*64)
        self.assertEqual(guest.target_hash(changed), guest.target_hash(f.options))
        self.assertNotEqual(guest.target_hash(dict(changed, x50_certificate_sha256='c'*64)),
                            guest.target_hash(f.options))

    def test_worker_routes_guest_to_pinned_tpap_and_never_acs(self):
        f = self.fx
        self.assertEqual(f.controller.accept('guest_on'), 'accepted')
        def probe(host, **kwargs):
            self.assertEqual(kwargs['expected_sha256'], 'b'*64)
            self.assertTrue(kwargs['read_info'])
            self.assertNotIn('optimize', kwargs)
            self.assertNotIn('scan_only', kwargs)
            kwargs['_before_connect']()
            self.assertGreater(json.loads((f.data/'x50_auth_state.json').read_text())['next_login'], 0)
            kwargs['_login_verified']()
            return {'state':'readonly_verified', 'guest_action':kwargs['_guest_callback'](f.reader)}
        with patch.object(protocol, 'probe', side_effect=probe):
            result = run.process_job(f.controller, None, f.options, f.data, stop=SimpleNamespace(is_set=lambda:False, wait=f.wait))
        self.assertEqual(result['state'], 'guest_enabled_verified')
        self.assertEqual(len(f.write_calls), 1)
        self.assertEqual(f.controller.report, {})
        self.assertNotIn('PRIVATE', (f.data/'guest_wifi_report.json').read_text())

    def test_certificate_or_connection_failure_never_runs_guest_callback(self):
        f = self.fx
        f.controller.accept('guest_on')
        job = f.controller.take()
        with patch.object(protocol, 'probe', return_value={'state':'certificate_pin_mismatch'}):
            result = backend.run_probe(f.options, f.data, run.save, guest_job=job,
                                       guest_control=f.controller.guest)
        self.assertEqual(result['state'], 'guest_connection_failed')
        self.assertEqual(result['connection_state'], 'certificate_pin_mismatch')
        self.assertFalse(result['write_attempted'])
        self.assertEqual(f.write_calls, [])

    def test_monitor_adds_only_boolean_guest_projection_when_enabled(self):
        f = self.fx
        from test_x50_backend import reading
        snapshot = reading()
        snapshot['guest'] = guest.project(wlan(True))
        stop = threading.Event()
        with patch.object(protocol, 'probe', return_value=snapshot) as probe:
            client = backend.Client(f.options, f.data, run.save, stop)
            client.login()
            status = client.collect(1, 'test')
        self.assertTrue(probe.call_args.kwargs['_guest_read'])
        self.assertTrue(status['diagnostics']['guest']['enabled'])
        self.assertNotIn('PRIVATE', json.dumps(status))
        with patch.object(protocol, 'probe', return_value=reading()) as probe:
            backend.Client(dict(f.options, guest_control_enabled=False), f.data, run.save, stop).read()
        self.assertFalse(probe.call_args.kwargs['_guest_read'])

    def test_transport_write_gate_payload_and_single_attempt(self):
        channel = SimpleNamespace(send=Mock(), report={})
        reader = protocol.TMPReader(channel)
        params = guest.write_params(wlan(), True)
        body = json.dumps({'config_version':1, 'params':params}).encode()
        with self.assertRaises(protocol.ProtocolError):
            reader.send(guest.WRITE_WLAN, body)
        channel.send.assert_not_called()
        reader.allow_guest = True
        invalid = copy.deepcopy(params)
        invalid['band2_4']['host'] = {'enable':False}
        with self.assertRaises(ValueError):
            reader.guest_request(guest.WRITE_WLAN, invalid)
        channel.send.assert_not_called()
        reader.transaction = 1
        reader.send(guest.WRITE_WLAN, body)
        sent = channel.send.call_args.args[0]
        self.assertEqual(struct.unpack_from('>H', sent, 18)[0], 0x4008)
        self.assertEqual(json.loads(sent[36:])['params'], params)
        reader.send(guest.WRITE_WLAN, b'', pull=True, offset=10)
        reader.transaction += 1
        with self.assertRaises(protocol.ProtocolError):
            reader.send(guest.WRITE_WLAN, body)
        self.assertEqual(channel.send.call_count, 2)

    def test_public_request_cannot_send_guest_write(self):
        reader = protocol.TMPReader(Mock())
        reader.allow_guest = True
        with self.assertRaises(protocol.ProtocolError):
            reader.request(guest.WRITE_WLAN, 10)

    def test_guest_callback_requires_authenticated_read_mode(self):
        with patch.object(protocol.socket, 'create_connection') as connect:
            with self.assertRaises(ValueError):
                protocol.probe('192.168.68.1', _guest_callback=Mock())
        connect.assert_not_called()

    def test_encrypted_guest_write_and_readbacks_on_one_authenticated_session(self):
        from test_x50_tls_probe import AuthenticationProbeTests, ReadOnlyPeer
        f = self.fx
        f.controller.accept('guest_on')
        job = f.controller.take()
        peer = ReadOnlyPeer(allow_guest=True, responses={
            0x4009: {'error_code':0,'result':wlan()},
            0x400F: {'error_code':0,'result':f.profile},
            0x40A0: {'error_code':0,'result':f.mode},
            0x4008: {'error_code':0,'result':{},'padding':'x'*80}})
        def callback(reader):
            return guest.execute(reader, f.options, f.data, f.controller.guest, job,
                                 wait=f.wait, clock=lambda:f.now)
        result, tls, _ = AuthenticationProbeTests().run_server(read_info=True, channel_peer=peer,
                                               guest_callback=callback, guest_read=True)
        self.assertEqual(result['guest_action']['state'], 'guest_enabled_verified')
        self.assertEqual(peer.requests.count(0x4008), 1)
        self.assertNotIn(0x424F, peer.requests)
        self.assertNotIn(0x4250, peer.requests)
        self.assertIn(0x4008, peer.pulls)
        self.assertFalse(result['guest']['enabled'])
        self.assertTrue(result['guest_action']['observed']['enabled'])
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_guest_callback_never_runs_without_verified_certificate_and_password(self):
        from test_x50_tls_probe import AuthenticationProbeTests, ReadOnlyPeer
        for args in ({'pin':'0'*64}, {'password':'wrong'}):
            callback = Mock()
            peer = ReadOnlyPeer()
            AuthenticationProbeTests().run_server(read_info=True, channel_peer=peer,
                                                  guest_callback=callback, **args)
            callback.assert_not_called()
            self.assertEqual(peer.requests, [])

    def test_previous_x50_apply_cooldown_and_uncertainty_block_guest(self):
        f = self.fx
        report = {'apply_command_attempted':True, 'apply_attempted_at':f.now - 1000,
                  'verification':{'state':'stable'}}
        journal = {'actions':{'prior':{'target_hash':guest.target_hash(f.options),'report':report}}}
        with patch.object(backend, 'load_journal', return_value=(None,journal)):
            self.assertEqual(f.controller.accept('guest_on'), 'apply_cooldown')
            report['verification']['state'] = 'unconfirmed'
            self.assertEqual(f.controller.accept('guest_on'), 'previous_apply_unverified')
        self.assertEqual(f.write_calls, [])

    def test_restart_never_replays_guest(self):
        f = self.fx
        self.assertEqual(f.controller.accept('guest_on'), 'accepted')
        restarted = Controller(f.options, f.data, run.save, clock=lambda:f.now)
        self.assertIsNone(restarted.take())
        self.assertEqual(restarted.guest.state['result'], 'interrupted_no_replay')
        self.assertEqual(restarted.accept('guest_on'), 'guest_cooldown')

    def test_shared_lock_blocks_x50_optimization_while_guest_is_active(self):
        from m9_actions import action_lock
        f = self.fx
        options = dict(f.options, tmp_action='optimize', tmp_optimize_request_id='new')
        with action_lock(f.data), patch.object(protocol,'run_action') as action:
            result = backend.run_probe(options, f.data, run.save)
        self.assertEqual(result['state'],'action_in_progress')
        action.assert_not_called()
