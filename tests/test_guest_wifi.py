"""Guest pilot contract: actual payloads, admission, readback and failure recovery."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ezmart_deco_optimizer'))
import guest_wifi as guest
from ha_control import Controller
from mqtt_bridge import Bridge, discovery, ENTITIES
import run
import tmp_readonly as tmp


def wlan(on=False):
    return {band: {'host': {'ssid': 'PRIVATE_MAIN', 'password': 'PRIVATE_PASSWORD', 'enable': True},
                   'guest': {'enable': on, 'ssid': 'PRIVATE_GUEST', 'password': 'PRIVATE_PASSWORD',
                             'host_isolation': False, 'encryption_mode': 'wpa2', 'access_duration': '-1'},
                   'backhaul': {'channel': 9}} for band in guest.BANDS}


class GuestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.now = 20000
        self.options = dict(host='192.168.68.4', tmp_host_key_sha256='a'*64,
                            tmp_enabled=True, mqtt_enabled=True, mqtt_commands_enabled=True,
                            guest_control_enabled=True, tmp_scan_backend='m9_acs')
        self.controller = self.make()
        self.monitor()
        self.raw = wlan()
        self.profile = {'device_list': [{'device_model': 'M9Plus', 'hardware_ver': '2.0',
                                       'software_ver': guest.FIRMWARE, 'role': 'master'}]}
        self.mode = {'mode': 'FAP'}
        self.snapshot = lambda: copy.deepcopy(self.raw)
        self.read_calls = []
        self.write_calls = []
        self.change = True
        self.lost_ack = False
        self.reader = SimpleNamespace(guest_request=self.request)

    def make(self, options=None):
        return Controller(options or self.options, self.data, run.save, lambda: self.now)

    def monitor(self, controller=None, raw=None):
        (controller or self.controller).set_monitor({'nodes': [], 'mode': 'FAP', 'diagnostics': {
            'guest': dict(guest.project(raw or wlan()), updated_at=self.now)}}, True)

    def wait(self, seconds):
        self.now += seconds
        return False

    def request(self, op, params=None):
        if op in guest.READ_OPS:
            self.assertIsNone(params)
            self.read_calls.append(op)
            result = self.snapshot() if op == guest.READ_WLAN else self.profile if op == guest.READ_DEVICES else self.mode
            return json.dumps({'error_code': 0, 'result': result})
        self.assertEqual(op, guest.WRITE_WLAN)
        self.assertTrue(json.loads((self.data/'guest_wifi_state.json').read_text())['write_attempted'])
        self.write_calls.append(copy.deepcopy(params))
        if self.change:
            for band in guest.BANDS:
                self.raw[band]['guest']['enable'] = params[band]['guest']['enable']
        if self.lost_ack:
            raise TimeoutError('PRIVATE')
        return json.dumps({'error_code': 0})

    def execute(self, action='guest_on', snapshot=None):
        if snapshot is not None:
            self.snapshot = snapshot
        self.assertEqual(self.controller.accept(action), 'accepted')
        self.job = self.controller.take()
        result = guest.execute(self.reader, self.options, self.data, self.controller.guest, self.job,
                               wait=self.wait, clock=lambda: self.now)
        self.controller.finish(result)
        return result

    def test_enable_and_disable_only_send_boolean_guest_fields_and_verify_twice(self):
        for action, expected in (('guest_on', True), ('guest_off', False)):
            result = self.execute(action)
            self.assertEqual(result['state'], 'guest_enabled_verified' if expected else 'guest_disabled_verified')
            self.assertTrue(result['acknowledged'])
            self.assertEqual(self.write_calls[-1], {band: {'guest': {'enable': expected, 'host_isolation': False}}
                                                   for band in guest.BANDS})
            self.assertEqual(self.controller.view()['guest_enabled'], expected)
            self.assertEqual(self.raw['band2_4']['host'], wlan()['band2_4']['host'])
            self.assertNotIn('PRIVATE', json.dumps(result))
            self.now += 61
            self.monitor(raw=self.raw)
        self.assertEqual(len(self.write_calls), 2)

    def test_lost_ack_uses_readback_without_resending(self):
        self.lost_ack = True
        result = self.execute()
        self.assertEqual(result['state'], 'guest_state_confirmed_without_ack')
        self.assertFalse(result['acknowledged'])
        self.assertEqual(len(self.write_calls), 1)

    def test_ack_without_state_change_is_not_success_and_never_retried(self):
        self.change = False
        result = self.execute()
        self.assertEqual(result['state'], 'guest_outcome_uncertain')
        self.assertFalse(self.controller.view()['guest_enabled'])
        self.assertEqual(len(self.write_calls), 1)

    def test_guest_session_uses_full_tmp_configuration_not_incomplete_https_view(self):
        # The M9 web view reports enable/credentials but omits isolation. It is
        # sufficient for monitoring, not for constructing a preserving write.
        web_view = wlan()
        for band in guest.BANDS:
            web_view[band]['guest'].pop('host_isolation')
        self.monitor(raw=web_view)
        self.assertEqual(guest.project(web_view)['state'], 'available')
        with self.assertRaises(ValueError):
            guest.write_params(web_view, True)
        self.options.update(tmp_host_key_sha256=hashlib.sha256(b'public-key').hexdigest(),
                            tmp_email='owner@example.com', tmp_password='PRIVATE_PASSWORD')
        self.assertEqual(self.controller.accept('guest_on'), 'accepted')
        job = self.controller.take()
        transport = Mock()
        transport.is_active.return_value = True
        transport.is_authenticated.return_value = True
        transport.get_remote_server_key.return_value.asbytes.return_value = b'public-key'
        self.reader.request = Mock(return_value=b'a'*32)
        https_snapshot = Mock(return_value=web_view)
        with patch.dict(sys.modules, {'paramiko': SimpleNamespace(Transport=Mock(return_value=transport))}), \
                patch.object(tmp.socket, 'create_connection', return_value=Mock()), \
                patch.object(tmp, 'Reader', return_value=self.reader):
            result = tmp.run_probe(self.options, self.data, run.save, clock=lambda: self.now,
                                   channel_snapshot=https_snapshot, guest_job=job,
                                   guest_control=self.controller.guest, wait=self.wait)
        self.controller.finish(result)
        self.assertEqual(result['state'], 'guest_enabled_verified')
        self.assertEqual(self.read_calls, [guest.READ_DEVICES, guest.READ_MODE,
                                          guest.READ_WLAN, guest.READ_WLAN, guest.READ_WLAN])
        self.assertEqual(len(self.write_calls), 1)
        self.assertFalse(self.reader.allow_guest)
        self.reader.request.assert_called_once_with(tmp.ALLOC)
        https_snapshot.assert_not_called()
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('PRIVATE', (self.data/'guest_wifi_state.json').read_text())

    def test_tmp_configuration_read_failure_does_not_send_or_expose_secrets(self):
        result = self.execute(snapshot=Mock(side_effect=TimeoutError('PRIVATE_PASSWORD')))
        self.assertEqual(result['state'], 'guest_configuration_unavailable')
        self.assertEqual(result['stage'], 'configuration_read')
        self.assertFalse(result['write_attempted'])
        self.assertEqual(self.write_calls, [])
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_incomplete_tmp_configuration_still_blocks_write(self):
        self.raw['band5_1']['guest'].pop('host_isolation')
        result = self.execute()
        self.assertEqual(result['state'], 'guest_configuration_unavailable')
        self.assertEqual(result['stage'], 'configuration_validation')
        self.assertFalse(result['write_attempted'])
        self.assertEqual(self.write_calls, [])

    def test_failed_tmp_readbacks_never_confirm_or_resend(self):
        def snapshot():
            if self.write_calls:
                raise TimeoutError('PRIVATE_PASSWORD')
            return copy.deepcopy(self.raw)
        result = self.execute(snapshot=snapshot)
        self.assertEqual(result['state'], 'guest_outcome_uncertain')
        self.assertEqual(len(self.write_calls), 1)
        self.assertEqual(self.read_calls.count(guest.READ_WLAN), 13)
        self.assertFalse(result['observed']['enabled'])
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_existing_desired_state_skips_write(self):
        self.raw = wlan(True)
        result = self.execute()
        self.assertEqual(result['state'], 'guest_already_set')
        self.assertFalse(result['write_attempted'])
        self.assertEqual(self.write_calls, [])

    def test_mixed_band_state_reads_on_but_disable_changes_both(self):
        self.raw['band5_1']['guest']['enable'] = True
        self.monitor(raw=self.raw)
        self.assertTrue(self.controller.view()['guest_enabled'])
        self.assertEqual(self.execute('guest_off')['state'], 'guest_disabled_verified')

    def test_settings_change_never_claims_preservation_or_rolls_back(self):
        count = 0
        def snapshot():
            nonlocal count
            count += 1
            raw = copy.deepcopy(self.raw)
            if count > 1:
                raw['band2_4']['host']['password'] = 'CHANGED_PRIVATE'
            return raw
        result = self.execute(snapshot=snapshot)
        self.assertEqual(result['state'], 'guest_settings_changed')
        self.assertEqual(len(self.write_calls), 1)
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_profile_gate_rejects_unknown_firmware_router_or_non_m9_master(self):
        for key, value in (('device_model', 'X50'), ('hardware_ver', '1.0'), ('software_ver', 'other')):
            original = self.profile['device_list'][0][key]
            self.profile['device_list'][0][key] = value
            self.assertFalse(guest.supported_profile(self.reader))
            self.profile['device_list'][0][key] = original
        self.mode['mode'] = 'Router'
        self.assertEqual(self.execute()['state'], 'guest_profile_not_validated')
        self.assertEqual(self.write_calls, [])

    def test_partial_or_invalid_guest_config_cannot_be_used_for_a_write(self):
        for mutation in (lambda r: r['band2_4']['guest'].pop('host_isolation'),
                         lambda r: r['band5_1']['guest'].update(enable='false'),
                         lambda r: r['band2_4']['guest'].update(ssid=''),
                         lambda r: r['band5_1']['guest'].update(host_isolation=True),
                         lambda r: r['band2_4']['host'].pop('password')):
            raw = wlan()
            mutation(raw)
            with self.assertRaises(ValueError):
                guest.write_params(raw, True)
        self.assertIsNone(guest.project({'band2_4': {'guest': {'enable': True}}})['enabled'])

    def test_unavailable_snapshot_and_stop_do_not_dispatch(self):
        result = self.execute(snapshot=lambda: {})
        self.assertEqual(result['state'], 'guest_configuration_unavailable')
        self.assertEqual(self.write_calls, [])
        self.now += 61
        self.monitor()
        self.snapshot = lambda: copy.deepcopy(self.raw)
        self.controller.accept('guest_on')
        job = self.controller.take()
        result = guest.execute(self.reader, self.options, self.data, self.controller.guest, job,
                               should_stop=lambda: True, wait=self.wait)
        self.assertEqual(result['state'], 'guest_cancelled')
        self.assertEqual(self.write_calls, [])

    def test_guest_timer_counter_can_change_but_isolation_and_duration_are_preserved(self):
        for band in guest.BANDS:
            self.raw[band]['guest'].update(host_isolation=True, access_duration=3600, remain_time=0)
        def snapshot():
            raw = copy.deepcopy(self.raw)
            for band in guest.BANDS:
                raw[band]['guest']['remain_time'] = self.now
            return raw
        result = self.execute(snapshot=snapshot)
        self.assertEqual(result['state'], 'guest_enabled_verified')
        self.assertTrue(self.write_calls[0]['band2_4']['guest']['host_isolation'])
        self.assertNotIn('access_duration', json.dumps(self.write_calls))

    def test_pending_optimization_application_blocks_guest_command(self):
        from m9_actions import target_hash
        report = {'request_id': 'old', 'state': 'apply_dispatch_uncertain', 'started_at': 100,
                  'apply_command_attempted': True, 'apply_attempted_at': 100,
                  'channels_before_apply': {'state': 'available', 'data': {'band5_2': {'channel': 149}}}}
        run.save(self.data/'tmp_optimize_journal.json', {'version': 1, 'actions': {
            'old': {'target_hash': target_hash(self.options), 'report': report}}})
        self.assertEqual(self.controller.accept('guest_on'), 'previous_apply_unverified')
        self.assertIsNone(self.controller.pending)

    def test_storage_failure_prevents_write_and_restart_never_replays(self):
        self.controller.accept('guest_on')
        job = self.controller.take()
        self.controller.guest.save = Mock(side_effect=OSError('PRIVATE'))
        with self.assertRaises(OSError):
            guest.execute(self.reader, self.options, self.data, self.controller.guest, job,
                          wait=self.wait)
        self.assertEqual(self.write_calls, [])
        restarted = self.make()
        self.monitor(restarted)
        self.assertIsNone(restarted.take())
        self.assertEqual(restarted.guest.state['result'], 'interrupted_no_replay')
        self.assertEqual(restarted.accept('guest_on'), 'scheduled')

    def test_cooldown_schedules_only_latest_intent_then_consumes_once(self):
        self.execute('guest_on')
        self.assertEqual(self.controller.accept('guest_off'), 'scheduled')
        self.assertEqual(self.controller.accept('guest_on'), 'scheduled')
        self.assertEqual(self.controller.accept('guest_off'), 'scheduled')
        self.assertEqual(self.controller.view()['guest_details']['scheduled_action'], 'guest_off')
        self.assertIsNone(self.controller.dispatch_scheduled_guest())
        self.assertIsNone(self.controller.pending)
        self.now = self.controller.guest.state['next_allowed']
        self.assertEqual(self.controller.dispatch_scheduled_guest(), 'accepted')
        self.assertIsNone(self.controller.scheduled_guest)
        job = self.controller.take()
        self.assertEqual(job['action'], 'guest_off')
        result = guest.execute(self.reader, self.options, self.data, self.controller.guest, job,
                               wait=self.wait, clock=lambda:self.now)
        self.controller.finish(result)
        self.controller.dispatch_scheduled_guest()
        self.assertIsNone(self.controller.pending)
        self.assertEqual(len(self.write_calls), 2)
        self.assertFalse(result['observed']['enabled'])

    def test_unsent_schedule_survives_restart_and_keeps_target(self):
        self.execute('guest_on')
        self.assertEqual(self.controller.accept('guest_off'), 'scheduled')
        restarted = self.make()
        self.assertEqual(restarted.scheduled_guest['action'], 'guest_off')
        self.assertIsNone(restarted.pending)
        self.now = restarted.guest.state['next_allowed']
        self.assertEqual(restarted.dispatch_scheduled_guest(), 'accepted')
        self.assertEqual(restarted.pending['action'], 'guest_off')
        self.assertIsNone(json.loads((self.data/'guest_wifi_queue.json').read_text())['request'])

    def test_restart_after_dispatch_does_not_resume_or_replay_schedule(self):
        self.execute('guest_on')
        self.controller.accept('guest_off')
        self.now = self.controller.guest.state['next_allowed']
        self.controller.dispatch_scheduled_guest()
        restarted = self.make()
        self.assertIsNone(restarted.scheduled_guest)
        self.assertIsNone(restarted.pending)
        self.assertEqual(restarted.guest.state['result'], 'interrupted_no_replay')

    def test_schedule_cancelled_when_expired_disabled_or_target_changed(self):
        self.execute('guest_on')
        self.controller.accept('guest_off')
        self.now = self.controller.scheduled_guest['expires_at'] + 1
        self.controller.dispatch_scheduled_guest()
        self.assertIsNone(self.controller.scheduled_guest)
        self.assertIsNone(self.controller.pending)
        self.controller.guest.state['next_allowed'] = self.now + 60
        self.controller.accept('guest_off')
        self.options['guest_control_enabled'] = False
        self.controller.dispatch_scheduled_guest()
        self.assertIsNone(self.controller.scheduled_guest)
        self.options['guest_control_enabled'] = True
        self.controller.accept('guest_off')
        changed = self.make(dict(self.options, host='192.168.68.99'))
        self.assertIsNone(changed.scheduled_guest)
        self.assertIsNone(changed.pending)

    def test_storage_failure_cannot_dispatch_scheduled_write(self):
        self.execute('guest_on')
        self.controller.accept('guest_off')
        self.now = self.controller.guest.state['next_allowed']
        self.controller.save = Mock(side_effect=OSError('PRIVATE'))
        self.controller.dispatch_scheduled_guest()
        self.assertEqual(self.controller.fault, 'guest_queue_storage_error')
        self.assertIsNone(self.controller.scheduled_guest)
        self.controller.save.reset_mock()
        self.controller.dispatch_scheduled_guest()
        self.controller.save.assert_not_called()
        self.assertIsNone(self.controller.pending)
        self.assertEqual(len(self.write_calls), 1)

    def test_request_during_running_guest_waits_for_reserved_write_cooldown(self):
        self.controller.accept('guest_on')
        job = self.controller.take()
        self.assertEqual(self.controller.accept('guest_off'), 'scheduled')
        self.now += 20
        result = guest.execute(self.reader, self.options, self.data, self.controller.guest, job,
                               wait=self.wait, clock=lambda:self.now)
        self.controller.finish(result)
        self.controller.dispatch_scheduled_guest()
        self.assertIsNone(self.controller.pending)
        self.now = self.controller.guest.state['next_allowed']
        self.assertEqual(self.controller.dispatch_scheduled_guest(), 'accepted')
        self.assertEqual(self.controller.pending['action'], 'guest_off')

    def test_failed_active_guest_cancels_scheduled_intent_without_retry(self):
        self.controller.accept('guest_on')
        self.controller.take()
        self.assertEqual(self.controller.accept('guest_off'), 'scheduled')
        self.controller.finish({'state':'guest_outcome_uncertain', 'write_attempted':True})
        self.assertIsNone(self.controller.scheduled_guest)
        self.now += 100
        self.controller.dispatch_scheduled_guest()
        self.assertIsNone(self.controller.pending)
        self.assertEqual(self.write_calls, [])

    def test_same_job_cannot_dispatch_a_second_write(self):
        self.execute()
        self.raw = wlan()
        with self.assertRaises(ValueError):
            guest.execute(self.reader, self.options, self.data, self.controller.guest, self.job,
                          wait=self.wait)
        self.assertEqual(len(self.write_calls), 1)

    def test_guest_history_never_overwrites_optimization_ids_result_or_cooldown(self):
        self.controller.control.update(last_id='prior', last_request='optimization_not_needed', next_allowed=20300)
        self.controller.report = {'state': 'optimization_not_needed', 'request_id': 'prior'}
        before = copy.deepcopy(self.controller.control)
        self.execute()
        self.assertEqual(self.controller.control, before)
        view = self.controller.view()
        self.assertEqual(view['result'], 'optimization_not_needed')
        self.assertEqual(view['details']['request_id'], 'prior')
        self.assertEqual(view['guest_details']['result'], 'guest_enabled_verified')

    def test_guest_admission_blocks_disabled_stale_busy_and_auth_cooldown(self):
        for overrides in ({'guest_control_enabled': False}, {'deco_model': 'x50'},
                          {'tmp_enabled': False}, {'mqtt_commands_enabled': False}, {'mqtt_enabled': False}):
            control = self.make(dict(self.options, **overrides))
            self.monitor(control)
            self.assertNotEqual(control.accept('guest_on'), 'accepted')
        self.now += 61
        self.assertEqual(self.controller.guest_reason(), 'ready')
        self.monitor()
        run.save(self.data/'tmp_state.json', {'next_login': self.now + 900})
        self.assertEqual(self.controller.accept('guest_on'), 'tmp_auth_cooldown')
        run.save(self.data/'tmp_state.json', {'next_login': 0})
        self.assertEqual(self.controller.accept('guest_on'), 'accepted')
        self.assertEqual(self.controller.accept('guest_off'), 'scheduled')
        self.assertEqual(self.controller.accept('optimize'), 'busy')
        self.now += 61
        self.monitor()
        self.assertIsNone(self.controller.take())
        self.assertEqual(self.controller.guest.state['result'], 'request_expired')

    def test_polling_detects_external_changes_without_active_request_or_secrets(self):
        client = SimpleNamespace(get_device_list=lambda: [], get_device_mode=lambda: SimpleNamespace(workmode='FAP'),
                                 request=Mock(return_value=wlan(True)))
        with patch.object(tmp, 'run_probe') as active:
            status = run.collect(client, self.options, 1, set(), {}, {})
        self.assertTrue(status['diagnostics']['guest']['enabled'])
        self.assertNotIn('PRIVATE', json.dumps(status))
        self.assertEqual(client.request.call_args.args, ('admin/wireless', 'wlan', {'operation': 'read'}))
        active.assert_not_called()

    def test_inventory_timeout_does_not_block_fresh_guest_reading(self):
        client = SimpleNamespace(get_device_list=Mock(side_effect=TimeoutError('PRIVATE')),
                                 get_device_mode=lambda: SimpleNamespace(workmode='FAP'),
                                 request=Mock(return_value=wlan(True)))
        status = run.collect(client, self.options, 1, set(), {}, {})
        self.assertNotIn('nodes', status)
        self.assertNotIn('PRIVATE', json.dumps(status))
        status['diagnostics']['guest']['updated_at'] = self.now
        self.controller.set_monitor(status, False)
        self.assertTrue(self.controller.view()['guest_available'])
        self.assertIsNone(self.controller.view()['node_count'])
        self.assertEqual(self.controller.reason(), 'ready')
        self.assertEqual(self.controller.guest_reason(), 'ready')
        self.now += 61
        self.assertFalse(self.controller.view()['guest_details']['observation_fresh'])
        self.assertTrue(self.controller.view()['guest_available'])

    def test_failed_wlan_or_global_failure_disables_guest_even_after_success(self):
        self.controller.guest_observed = dict(guest.project(wlan(True)), updated_at=self.now)
        self.controller.set_monitor({'diagnostics': {'guest': {'state': 'unavailable',
                                                              'updated_at': self.now}}}, False)
        self.assertFalse(self.controller.view()['guest_details']['observation_fresh'])
        self.assertTrue(self.controller.view()['guest_available'])
        self.monitor()
        self.assertTrue(self.controller.view()['guest_available'])
        self.controller.set_monitor({}, False)
        self.assertFalse(self.controller.view()['guest_details']['observation_fresh'])
        self.assertTrue(self.controller.view()['guest_available'])
        self.assertEqual(self.controller.guest_reason(), 'ready')

    def test_inventory_auth_failure_still_aborts_independent_reads(self):
        class AuthenticationError(Exception):
            pass
        client = SimpleNamespace(get_device_list=Mock(side_effect=AuthenticationError('PRIVATE')),
                                 get_device_mode=Mock(), request=Mock())
        with self.assertRaises(AuthenticationError):
            run.collect(client, self.options, 1, set(), {}, {})
        client.get_device_mode.assert_not_called()
        client.request.assert_not_called()

    def test_explicit_guest_request_with_failed_inventory_uses_live_tmp_preflight(self):
        self.controller.set_monitor({}, False)
        self.assertEqual(self.controller.accept('guest_on'), 'accepted')
        job = self.controller.take()
        self.assertIsNotNone(job)
        self.assertEqual(guest.execute(self.reader, self.options, self.data, self.controller.guest,
                                      job, wait=self.wait, clock=lambda: self.now)['state'],
                         'guest_enabled_verified')
        self.assertEqual(len(self.write_calls), 1)

    def test_guest_last_observation_survives_monitor_failure_without_becoming_fresh(self):
        self.monitor(raw=wlan(True))
        self.controller.set_monitor({}, False)
        view = self.controller.view()
        self.assertTrue(view['guest_enabled'])
        self.assertFalse(view['guest_details']['observation_fresh'])
        self.assertTrue(view['guest_available'])
        self.controller.guest_observed = dict(guest.project(wlan(False)), updated_at=self.now + 10)
        self.monitor(raw=wlan(True))
        self.assertFalse(self.controller.view()['guest_enabled'])

    def test_main_processes_guest_during_https_cooldown_without_login(self):
        self.options.update(mqtt_enabled=True, password='PRIVATE', tmp_email='owner@example.test',
                            tmp_password='PRIVATE')
        self.controller.set_monitor({}, False)
        self.assertEqual(self.controller.accept('guest_on'), 'accepted')
        options = self.data / 'options.json'
        run.save(options, self.options)
        run.save(self.data / 'state.json', {'next_login': self.now + 900})
        # main() uses /data paths; route them to this isolated fixture.
        real_path = Path
        def path(value):
            return self.data if value == '/data' else real_path(value)
        stop = threading.Event()
        def execute(options, data, save, **kwargs):
            self.assertEqual(kwargs['guest_job']['action'], 'guest_on')
            stop.set()
            return {'state': 'guest_enabled_verified'}
        with patch.object(run, 'Path', side_effect=path), patch.object(run, 'STOP', stop),                 patch('ha_control.Controller', return_value=self.controller),                 patch('mqtt_bridge.Bridge', return_value=SimpleNamespace(start=lambda: None,
                      thread=SimpleNamespace(join=lambda **kw: None))),                 patch.dict(sys.modules, {'tplink_deco_api': SimpleNamespace(DecoClient=Mock())}),                 patch.object(tmp, 'run_probe', side_effect=execute):
            run.main()
        self.assertEqual(self.controller.guest.state['result'], 'guest_enabled_verified')

    def test_worker_routes_guest_separately_from_optimize(self):
        self.controller.accept('guest_on')
        with patch.object(tmp, 'run_probe', return_value={'state': 'guest_enabled_verified'}) as probe:
            result = run.process_job(self.controller, object(), self.options, self.data, stop=threading.Event())
        self.assertEqual(probe.call_args.kwargs['guest_job']['action'], 'guest_on')
        self.assertIs(probe.call_args.kwargs['guest_control'], self.controller.guest)
        self.assertEqual(result['action_type'], 'guest_wifi')
        self.assertEqual(self.controller.report, {})
        self.assertFalse(self.controller.control['inflight'])

    def test_x50_and_startup_cannot_enter_guest_transport(self):
        with patch.object(tmp.socket, 'create_connection') as connect:
            result = tmp.run_probe(dict(self.options, deco_model='x50'), self.data, run.save,
                                   guest_job={'action': 'guest_on'}, guest_control=self.controller.guest)
            self.assertEqual(result['state'], 'guest_control_disabled')
            result = tmp.run_probe(dict(self.options, tmp_action='guest_on'), self.data, run.save)
            self.assertEqual(result['state'], 'invalid_action')
        connect.assert_not_called()

    def test_switch_discovery_confirmed_state_and_old_entities_unchanged(self):
        old = discovery('id', 'base', 'version', 'nonce')
        new = discovery('id', 'base', 'version', 'nonce', guest_enabled=True)
        self.assertEqual(new[:len(ENTITIES)], old)
        switch = new[-1][1]
        self.assertEqual(switch['default_entity_id'], 'switch.ezmart_deco_visitantes')
        self.assertFalse(switch['optimistic'])
        self.assertFalse(switch['retain'])
        self.assertEqual(json.loads(switch['payload_on']), {'action': 'guest_on', 'session': 'nonce'})
        self.assertEqual(json.loads(switch['payload_off'])['action'], 'guest_off')
        self.assertEqual(len(discovery('id', 'base', 'version', 'nonce', model='x50', guest_enabled=True)), len(old) + 1)

    def test_mqtt_guest_commands_require_live_session_and_no_retain_or_dup(self):
        bridge = Bridge(self.options, self.data, run.save, self.controller, threading.Event(), 'test')
        bridge.session = 'live'
        bridge.connected.set()
        valid = SimpleNamespace(topic=bridge.base+'/command', payload=b'{"action":"guest_on","session":"live"}',
                                retain=False, dup=False)
        for override in ({'retain': True}, {'dup': True}, {'payload': b'{"action":"guest_on","session":"old"}'}):
            msg = copy.copy(valid)
            for key, value in override.items():
                setattr(msg, key, value)
            bridge.on_message(None, None, msg)
        self.assertIsNone(self.controller.pending)
        bridge.on_message(None, None, valid)
        self.assertEqual(self.controller.pending['action'], 'guest_on')

    def test_wire_payload_and_opcode_allowlist(self):
        channel = Mock()
        reader = tmp.Reader(channel)
        params = guest.write_params(wlan(), True)
        with self.assertRaises(tmp.ProtocolError):
            reader.guest_request(guest.WRITE_WLAN, params)
        with self.assertRaises(tmp.ProtocolError):
            tmp.packet(guest.WRITE_WLAN, b'', 0, 1)
        reader.allow_guest = True
        bad = copy.deepcopy(params)
        bad['band2_4']['host'] = {'enable': False}
        with self.assertRaises(ValueError):
            reader.guest_request(guest.WRITE_WLAN, bad)
        with self.assertRaises(tmp.ProtocolError):
            reader.request(guest.WRITE_WLAN)
        channel.sendall.assert_not_called()
        body = b'{"error_code":0}'
        payload = struct.pack('>BBHBBHIII', 1, 2, guest.WRITE_WLAN, 5, 0, 0, zlib.crc32(body), len(body), 0) + body
        head = struct.pack('>BBBBHHI', 1, 1, 5, 0, len(payload), 0, 1)
        response = bytearray(head + struct.pack('>I', zlib.crc32(head + bytes.fromhex('5a6b7c8d') + payload)) + payload)
        def receive(n):
            out = bytes(response[:n])
            del response[:n]
            return out
        channel.recv.side_effect = receive
        self.assertEqual(reader.guest_request(guest.WRITE_WLAN, params), body)
        sent = channel.sendall.call_args.args[0]
        self.assertEqual(struct.unpack_from('>H', sent, 18)[0], guest.WRITE_WLAN)
        self.assertEqual(json.loads(sent[36:])['params'], params)
        self.assertNotIn(b'PRIVATE', sent)

    def test_full_wlan_read_requires_guest_gate_and_empty_parameters(self):
        reader = tmp.Reader(Mock())
        with self.assertRaises(tmp.ProtocolError):
            reader.guest_request(guest.READ_WLAN)
        with self.assertRaises(tmp.ProtocolError):
            tmp.packet(guest.READ_WLAN, b'', 0, 1)
        reader.allow_guest = True
        with patch.object(reader, '_exchange', return_value=b'{}') as exchange:
            with self.assertRaises(tmp.ProtocolError):
                reader.guest_request(guest.READ_WLAN, {'guest': {'enable': True}})
            exchange.assert_not_called()
            self.assertEqual(reader.guest_request(guest.READ_WLAN), b'{}')
            exchange.assert_called_once_with(guest.READ_WLAN, {}, 20)
        packet = tmp.packet(guest.READ_WLAN, b'{}', 0, 1, allow_guest=True)
        self.assertEqual(struct.unpack_from('>H', packet, 18)[0], 0x4009)


if __name__ == '__main__':
    unittest.main()
