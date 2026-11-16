"""Exact-profile M5 pilot: preserving guest writes and conditional ACS application."""
import json
from unittest.mock import Mock
import unittest

import test_guest_wifi as guest_fixture
import test_m9_apply as apply_fixture
import guest_wifi
import ha_control
import m5_profile
import mqtt_bridge
import run
import tmp_readonly


class M5GuestTests(unittest.TestCase):
    def setUp(self):
        self.fx = guest_fixture.GuestTests('test_existing_desired_state_skips_write')
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        f = self.fx
        f.options['deco_model'] = 'm5'
        f.profile['device_list'][0].update(device_model='M5', hardware_ver='3.0',
                                         software_ver=m5_profile.FIRMWARE)
        f.mode = {'mode':'FAP'}
        f.controller = ha_control.Controller(f.options, f.data, run.save, clock=lambda:f.now)
        def monitor(controller=None, raw=None):
            node = f.profile['device_list'][0]
            snapshot = {'nodes':[{'model':node['device_model'], 'hardware':node['hardware_ver'],
                        'firmware':node['software_ver'], 'role':node['role']}], 'mode':'FAP',
                        'diagnostics':{'guest':dict(guest_wifi.project(raw or guest_fixture.wlan()),
                                                    updated_at=f.now)}}
            snapshot['profile_supported'] = m5_profile.supported_snapshot(snapshot)
            (controller or f.controller).set_monitor(snapshot, True)
        f.monitor = monitor
        f.monitor()

    def test_guest_30_enables_disables_and_preserves_configuration(self):
        self.fx.test_enable_and_disable_only_send_boolean_guest_fields_and_verify_twice()

    def test_guest_32_enables_disables_and_preserves_configuration(self):
        self.fx.profile['device_list'][0]['hardware_ver'] = '3.2'
        self.fx.monitor()
        self.fx.test_enable_and_disable_only_send_boolean_guest_fields_and_verify_twice()

    def test_live_profile_mismatch_never_writes_despite_cached_valid_profile(self):
        f = self.fx
        f.profile['device_list'][0]['hardware_ver'] = '1.0'
        self.assertEqual(f.execute()['state'], 'guest_profile_not_validated')
        self.assertEqual(f.write_calls, [])

    def test_m5_guest_uses_shared_ssh_transport_and_complete_tmp_preflight(self):
        self.fx.test_guest_session_uses_full_tmp_configuration_not_incomplete_https_view()

    def test_m5_scheduled_latest_intent_executes_once(self):
        self.fx.test_cooldown_schedules_only_latest_intent_then_consumes_once()


class M5OptimizationTests(unittest.TestCase):
    def fixture(self, hardware='3.0', needed=True):
        f = apply_fixture.ApplyTests('test_positive_recommendation_then_single_application_and_stable_channels')
        f.setUp()
        self.addCleanup(f.doCleanups)
        f.options['deco_model'] = 'm5'
        reader = apply_fixture.Reader(scan=apply_fixture.scan_result(needed))
        def metadata(opcode):
            return json.dumps({'error_code':0, 'result':{'device_list':[{
                'device_model':'M5', 'hardware_ver':hardware, 'software_ver':m5_profile.FIRMWARE,
                'role':'master'}]} if opcode == 0x400f else {'mode':'FAP'}}).encode()
        reader.profile_request = Mock(side_effect=metadata)
        calls = [0]
        def channels():
            calls[0] += 1
            return {'band2_4':{'host':{'channel':7 if calls[0] == 1 else 9}},
                    'band5_1':{'host':{'channel':44}}}
        return f, reader, channels

    def test_each_profile_applies_once_then_verifies_three_stable_observations(self):
        for hardware in ('3.0', '3.2'):
            with self.subTest(hardware=hardware):
                f, reader, channels = self.fixture(hardware)
                result = f.run_action(reader, snapshot=channels)
                self.assertEqual(result['state'], 'apply_acknowledged')
                self.assertEqual(result['verification']['stable_samples'], 3)
                self.assertEqual(reader.calls, [0x424f, 0x4250])
                self.assertEqual([c.args[0] for c in reader.profile_request.call_args_list], [0x400f, 0x40a0])
                self.assertNotIn('PRIVATE', json.dumps(result))

    def test_negative_recommendation_never_applies(self):
        f, reader, channels = self.fixture(needed=False)
        self.assertEqual(f.run_action(reader, snapshot=channels)['state'], 'optimization_not_needed')
        self.assertEqual(reader.calls, [0x424f])

    def test_unknown_profile_never_scans_or_applies(self):
        f, reader, channels = self.fixture(hardware='3.20')
        result = f.run_action(reader, snapshot=channels)
        self.assertEqual(result['state'], 'm5_profile_not_validated')
        self.assertEqual(result['profile_diagnostic']['master']['hardware_ver'], '3.20')
        self.assertEqual(reader.calls, [])

    def test_profile_metadata_errors_never_scan_or_apply(self):
        f, reader, channels = self.fixture()
        reader.profile_request.side_effect = TimeoutError('PRIVATE')
        result = f.run_action(reader, snapshot=channels)
        self.assertEqual(result['state'], 'm5_profile_not_validated')
        self.assertEqual(result['profile_diagnostic']['stage'], 'devices')
        self.assertEqual(result['profile_diagnostic']['error_type'], 'TimeoutError')
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertEqual(reader.calls, [])

    def test_metadata_transport_gate_cannot_enable_any_write(self):
        for opcode in (0x400f, 0x40a0):
            tmp_readonly.packet(opcode, b'{}', 1, 1, allow_profile_read=True)
        for opcode in (0x4008, 0x4250, 0x424f, 0x4266):
            with self.assertRaises(tmp_readonly.ProtocolError):
                tmp_readonly.packet(opcode, b'{}', 1, 1, allow_profile_read=True)

    def test_profiles_require_ap_exact_firmware_and_one_master(self):
        self.assertTrue(m5_profile.supported('M5', '3.0', m5_profile.FIRMWARE, 'FAP'))
        self.assertTrue(m5_profile.supported('M5', '3.2', m5_profile.FIRMWARE, 'AP'))
        for model, hw, fw, mode in [('M5','3.2',m5_profile.FIRMWARE,'Router'),
                                   ('M5','3.20',m5_profile.FIRMWARE,'FAP'),
                                   ('M5','3.0','1.9.6','FAP'), ('M9Plus','3.0',m5_profile.FIRMWARE,'FAP')]:
            self.assertFalse(m5_profile.supported(model, hw, fw, mode))
        self.assertFalse(m5_profile.supported_snapshot({'nodes':[], 'mode':'FAP'}))

    def test_profile_diagnostic_never_exports_raw_body_or_identifiers(self):
        reader = Mock()
        reader.profile_diagnostic = {'version':1, 'subversion':2, 'chunk_bytes':8156,
            'total_bytes':10528, 'offset':0, 'chunk_crc_matches':False, 'body':'PRIVATE'}
        reader.profile_request.side_effect = tmp_readonly.ProtocolError('PRIVATE')
        diagnostic = {}
        self.assertFalse(m5_profile.read_supported(reader, diagnostic))
        self.assertEqual(diagnostic['framing']['chunk_bytes'], 8156)
        self.assertNotIn('PRIVATE', json.dumps(diagnostic))

    def test_guest_switch_is_discovered_with_existing_entity_id(self):
        configs = mqtt_bridge.discovery('pilot', 'test', run.VERSION, 'session', 'm5', True)
        switches = [config for _, config in configs if config.get('default_entity_id') == 'switch.ezmart_deco_visitantes']
        self.assertEqual(len(switches), 1)


if __name__ == '__main__':
    unittest.main()
