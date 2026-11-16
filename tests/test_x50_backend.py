"""App admission, monitoring and reporting at the X50/M9 boundary."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ezmart_deco_optimizer'))
import ha_control
import mqtt_bridge
import run
import x50_backend as backend


def reading():
    return {'state': 'readonly_verified', 'reads': {
        'mode': {'state': 'available', 'api_code': 0, 'data': {'mode': 'Router'}},
        'channels': {'state': 'available', 'api_code': 0, 'data': {
            'band2_4': {'radio': {'htmode': 'HT40'}, 'backhaul': {'channel': 4}},
            'band5_1': {'radio': {'htmode': 'HT80'}, 'backhaul': {'channel': 36}}}},
        'devices': {'state': 'available', 'api_code': 0, 'data': {'node_count': 2, 'nodes': [
            {'model': 'X50', 'hardware': '1.30', 'firmware': '1.8.4 Build 26051413 Rel. 27460', 'role': role}
            for role in ('master', 'slave')]}}}}


class X50AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ezmart-x50-app-', dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.stop = threading.Event()
        self.now = 20000
        self.options = dict(deco_model='x50', host='192.168.68.1', x50_certificate_sha256='a'*64,
                            tmp_email='owner@example.test', tmp_password='PRIVATE',
                            tmp_enabled=True, mqtt_enabled=True, mqtt_commands_enabled=True, poll_seconds=60)

    def control(self):
        control = ha_control.Controller(self.options, self.data, run.save, clock=lambda: self.now)
        control.set_monitor({'profile_supported': True, 'nodes': [], 'mode': 'Router'}, True)
        return control

    def record(self, accepted=True, stable=True, request_id='prior'):
        report = dict(request_id=request_id, state='apply_acknowledged', started_at=100,
                      apply_command_attempted=True, apply_attempted_at=110,
                      channels_before_apply=reading()['reads']['channels'],
                      apply_ack={'state': 'accepted' if accepted else 'unavailable', 'api_code': 0 if accepted else None},
                      verification={'state': 'stable' if stable else 'incomplete'})
        backend.protocol.atomic_json(self.data / 'x50_optimize_journal.json',
                                     {'version': 1, 'actions': {request_id: {
                                         'target': backend.target_hash(self.options), 'report': report}}})
        return report

    def test_monitor_uses_readonly_calls_and_cached_first_login(self):
        with patch.object(backend.protocol, 'probe', return_value=reading()) as probe:
            client = backend.Client(self.options, self.data, run.save, self.stop)
            client.login()
            first = run.collect(client, self.options, 1, set(), {}, {})
            self.assertEqual(probe.call_count, 1)
            second = run.collect(client, self.options, 2, set(), {}, {})
        self.assertEqual(probe.call_count, 2)
        for call in probe.call_args_list:
            self.assertTrue(call.kwargs['read_info'])
            self.assertNotIn('optimize', call.kwargs)
            self.assertNotIn('scan_only', call.kwargs)
        self.assertTrue(first['profile_supported'])
        self.assertEqual(second['mode'], 'Router')
        self.assertNotIn('performance', first['diagnostics'])

    def test_no_x50_startup_actions_even_with_legacy_manual_options(self):
        for mqtt in (False, True):
            self.assertFalse(run.startup_action_enabled(dict(self.options, mqtt_enabled=mqtt,
                                                           tmp_action='optimize', tmp_optimize_request_id='old')))

    def test_poll_snapshot_gates_unvalidated_firmware(self):
        value = reading()
        value['reads']['devices']['data']['nodes'][0]['firmware'] = 'unvalidated'
        with patch.object(backend.protocol, 'probe', return_value=value):
            client = backend.Client(self.options, self.data, run.save, self.stop)
            snapshot = client.collect(1, run.VERSION)
        control = self.control()
        control.set_monitor(snapshot, True)
        self.assertEqual(control.accept('optimize'), 'x50_profile_not_validated')

    def test_login_guard_is_durable_across_objects_and_only_proof_releases_it(self):
        guard = backend.LoginGuard(self.data, run.save, clock=lambda: self.now)
        guard.before()
        self.assertEqual(json.loads(guard.path.read_text())['next_login'], self.now+900)
        fresh = backend.LoginGuard(self.data, run.save, clock=lambda: self.now)
        with self.assertRaises(backend.protocol.ProtocolError):
            fresh.before()
        guard.verified()
        fresh.before()

    def test_guard_storage_corruption_failure_and_stop_prevent_connection(self):
        for value in ({}, {'next_login': True}, {'next_login': -1}, {'next_login': float('nan')}):
            (self.data/'x50_auth_state.json').write_text(json.dumps(value))
            with self.assertRaises(backend.protocol.ActionStorageError):
                backend.LoginGuard(self.data, run.save).before()
        with self.assertRaises(backend.protocol.ActionCancelled):
            backend.LoginGuard(self.data, run.save, should_stop=lambda: True).before()
        (self.data/'x50_auth_state.json').write_text('{"next_login": 0}')
        def fail(*args):
            raise OSError('PRIVATE')
        with self.assertRaises(backend.protocol.ActionStorageError):
            backend.LoginGuard(self.data, fail).before()

    def test_auth_cooldown_blocks_both_buttons(self):
        self.record()
        run.save(self.data/'x50_auth_state.json', {'next_login': self.now+100})
        control = self.control()
        self.assertEqual(control.reason(), 'tmp_auth_cooldown')
        self.assertEqual(control.reason('verify'), 'tmp_auth_cooldown')

    def test_x50_request_cooldown_is_30_minutes_and_restart_never_replays(self):
        control = self.control()
        self.assertEqual(control.accept('optimize'), 'accepted')
        self.assertEqual(control.control['next_allowed'], self.now+1800)
        fresh = self.control()
        self.assertEqual(fresh.control['last_request'], 'interrupted_no_replay')
        self.assertIsNone(fresh.take())
        self.assertEqual(fresh.reason(), 'request_cooldown')

    def test_uncertain_previous_apply_only_allows_verification(self):
        self.record(accepted=False)
        control = self.control()
        self.assertEqual(control.reason(), 'previous_apply_unverified')
        self.assertEqual(control.reason('verify'), 'ready')
        self.assertEqual(control.accept('verify'), 'accepted')
        job = control.take()
        self.assertEqual(job['request_id'], 'prior')
        self.assertEqual(job['action'], 'verify')

    def test_target_and_transport_journals_stay_separate(self):
        self.record()
        control = self.control()
        self.assertEqual(control.report['request_id'], 'prior')
        self.options['x50_certificate_sha256'] = 'b'*64
        different = self.control()
        self.assertEqual(different.report, {})
        self.assertIsNone(different.verify_id())
        self.assertFalse((self.data/'tmp_optimize_journal.json').exists())

    def test_observed_backhaul_is_published_with_scope_and_newer_timestamp(self):
        report = self.record()
        report.update(scan={'need_optimize': True}, conclusion='acknowledged_with_channel_change')
        report['verification'].update(last_available=reading()['reads']['channels'], last_available_at=self.now,
            channel_comparison={'observed_channels_changed': True, 'channel_changes': [
                {'band': 'band2_4', 'section': 'backhaul', 'before': 10, 'after': 4}]})
        control = self.control()
        control.progress(report)
        public = control.view()
        self.assertEqual((public['band2_4'], public['band5_1'], public['band5_2']), (4, 36, None))
        self.assertTrue(public['need_optimize'])
        self.assertTrue(public['channels_changed'])
        self.assertEqual(public['details']['channel_scope'], 'backhaul')
        self.assertEqual(public['details']['api_code'], 0)
        self.assertEqual(public['details']['verification'], 'stable')
        self.assertIsNone(public['cpu_usage'])
        self.assertNotIn('PRIVATE', json.dumps(public))
        # A failed later observation must not advance the age of the last good sample.
        report['verification']['observations'] = [{'state': 'timeout', 'at': self.now+10}]
        control.progress(report)
        self.assertEqual(control.view()['channels_at'], public['channels_at'])
        current = copy.deepcopy(reading()['reads']['channels'])
        current['updated_at'] = self.now+20
        current['data']['band2_4']['backhaul']['channel'] = 11
        control.set_monitor({'profile_supported': True, 'diagnostics': {'channels': current}}, True)
        self.assertEqual(control.view()['band2_4'], 11)

    def test_discovery_preserves_ids_but_labels_backhaul_and_model(self):
        m9 = mqtt_bridge.discovery('id', 'base', run.VERSION, 'session')
        x50 = mqtt_bridge.discovery('id', 'base', run.VERSION, 'session', 'x50')
        self.assertEqual([a for a, _ in m9], [a for a, _ in x50])
        self.assertEqual(len(x50), 22)
        channel = next(value for _, value in x50 if value['unique_id'].endswith('_canal_2_4'))
        self.assertEqual(channel['name'], 'Canal backhaul 2,4 GHz')
        self.assertIn('X50', channel['device']['model'])

    def test_worker_routes_to_x50_and_publishes_progress_after_storage(self):
        control = self.control()
        control.accept('optimize')
        def action(options, data, save, **kwargs):
            report = {'request_id': options['tmp_optimize_request_id'], 'state': 'optimization_not_needed'}
            save(data/'x50_optimize_report.json', report)
            self.assertEqual(control.report, report)
            self.assertTrue((data/'x50_optimize_report.json').exists())
            return report
        with patch.object(backend, 'run_probe', side_effect=action) as x50, \
                patch('tmp_readonly.run_probe') as m9:
            result = run.process_job(control, object(), self.options, self.data, stop=self.stop)
        x50.assert_called_once()
        m9.assert_not_called()
        self.assertEqual(result['state'], 'optimization_not_needed')
        self.assertFalse(control.busy)

    def test_runtime_action_uses_persisted_guard_and_own_journal(self):
        options = dict(self.options, tmp_action='optimize', tmp_optimize_request_id='new')
        def action(**kwargs):
            self.assertEqual(kwargs['journal'], self.data/'x50_optimize_journal.json')
            kwargs['_before_connect']()
            self.assertGreater(json.loads((self.data/'x50_auth_state.json').read_text())['next_login'], 0)
            kwargs['_login_verified']()
            kwargs['_progress']({'request_id': 'new', 'state': 'analyzing'})
            return {'state': 'optimization_not_needed'}
        with patch.object(backend.protocol, 'run_action', side_effect=action):
            self.assertEqual(backend.run_probe(options, self.data, run.save)['state'], 'optimization_not_needed')
        self.assertEqual(json.loads((self.data/'x50_auth_state.json').read_text())['next_login'], 0)
        self.assertNotIn('PRIVATE', (self.data/'x50_optimize_report.json').read_text())

    def test_minimal_config_and_optional_legacy_fields(self):
        config = yaml.safe_load((ROOT/'ezmart_deco_optimizer/config.yaml').read_text())
        defaults = config['options']
        self.assertEqual(len(defaults), 11)
        self.assertEqual(defaults["tmp_host_key_mode"], "automatic")
        self.assertFalse(defaults['mqtt_commands_enabled'])
        self.assertFalse(defaults['tmp_enabled'])
        for key in ('tmp_action', 'tmp_optimize_request_id', 'tmp_scan_request_id', 'mqtt_host', 'poll_seconds'):
            self.assertNotIn(key, defaults)
            self.assertTrue(config['schema'][key].endswith('?'))
        configured = backend.settings(dict(defaults, deco_model='x50', host='192.168.68.1',
                                          tmp_email='owner@example.test', tmp_password='PRIVATE',
                                          x50_certificate_sha256='a'*64))
        self.assertEqual(configured['timeout'], 10)
        self.assertEqual(configured['verify_timeout'], 120)
        self.assertEqual(config['version'], run.VERSION)

    def test_minimal_x50_starts_without_https_and_never_runs_legacy_startup_action(self):
        options = dict(self.options, tmp_action='optimize', tmp_optimize_request_id='old')
        options.pop('poll_seconds')
        run.save(self.data/'options.json', options)
        snapshots = []
        original_save = run.save
        def save(path, value):
            original_save(path, value)
            if path.name == 'status.json':
                snapshots.append(value)
                self.stop.set()
        sdk = SimpleNamespace(DecoClient=MagicMock())
        with patch.dict(sys.modules, {'tplink_deco_api': sdk}), \
                patch.object(run, 'Path', return_value=self.data), patch.object(run, 'STOP', self.stop), \
                patch.object(run, 'save', side_effect=save), \
                patch.object(mqtt_bridge, 'Bridge') as bridge, \
                patch.object(backend.protocol, 'probe', return_value=reading()) as probe, \
                patch.object(backend, 'run_probe') as action:
            self.assertEqual(run.main(), 0)
        self.assertEqual(snapshots[0]['backend'], 'x50_tpap')
        self.assertTrue(snapshots[0]['profile_supported'])
        self.assertEqual(bridge.call_args.args[0]['poll_seconds'], 60)
        sdk.DecoClient.assert_not_called()
        action.assert_not_called()
        self.assertEqual(probe.call_count, 1)

    def test_missing_model_keeps_existing_m9_runtime(self):
        options = dict(host='192.168.68.4', password='PRIVATE', mqtt_enabled=True, tmp_enabled=False)
        run.save(self.data/'options.json', options)
        client = MagicMock()
        client.get_device_list.return_value = [SimpleNamespace(device_model='M9Plus', software_ver='PUBLIC')]
        client.get_device_mode.return_value = SimpleNamespace(workmode='FAP')
        sdk = SimpleNamespace(DecoClient=MagicMock(return_value=client))
        snapshots = []
        original_save = run.save
        def save(path, value):
            original_save(path, value)
            if path.name == 'status.json':
                snapshots.append(value)
                self.stop.set()
        with patch.dict(sys.modules, {'tplink_deco_api': sdk}), \
                patch.object(run, 'Path', return_value=self.data), patch.object(run, 'STOP', self.stop), \
                patch.object(run, 'save', side_effect=save), patch.object(run, 'probe', return_value={}), \
                patch.object(mqtt_bridge, 'Bridge'), patch.object(backend, 'Client') as x50:
            self.assertEqual(run.main(), 0)
        sdk.DecoClient.assert_called_once_with('192.168.68.4', 'admin', 'PRIVATE', timeout=10)
        x50.assert_not_called()
        self.assertEqual(snapshots[0]['mode'], 'FAP')


if __name__ == '__main__':
    unittest.main()
