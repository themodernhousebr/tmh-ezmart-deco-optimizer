"""Admission, persistence and worker integration: no broker or router required."""
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ezmart_deco_optimizer'))
from ha_control import Controller
import m9_actions
import run
import tmp_readonly


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.now = 20000
        self.options = dict(host='192.168.68.4', tmp_host_key_sha256='a'*64, tmp_scan_backend='m9_acs',
                            tmp_enabled=True, mqtt_enabled=True, mqtt_commands_enabled=True,
                            request_cooldown_seconds=900, poll_seconds=30)
        self.control = self.make()
        self.healthy()

    def make(self, options=None):
        return Controller(options or self.options, self.data, run.save, clock=lambda: self.now)

    def healthy(self, control=None):
        (control or self.control).set_monitor({'nodes': [{'model': 'M9Plus', 'firmware': 'PUBLIC'}], 'mode': 'FAP'}, True)

    def test_boot_never_enqueues_a_job_or_replays_manual_configuration(self):
        self.options.update(tmp_action='optimize', tmp_optimize_request_id='manual-old')
        self.assertFalse(run.startup_action_enabled(self.options))
        self.assertIsNone(self.control.take())
        self.assertEqual(self.control.view()['state'], 'idle')
        self.assertTrue(self.control.view()['ready'])
        self.assertFalse(list(self.data.glob('*optimize*')))

    def test_legacy_manual_mode_remains_explicit(self):
        self.assertTrue(run.startup_action_enabled(dict(self.options, mqtt_enabled=False)))
        self.assertFalse(run.startup_action_enabled(dict(self.options, mqtt_enabled=False, tmp_enabled=False)))

    def test_admission_is_durable_and_second_request_never_queues(self):
        self.assertEqual(self.control.accept('optimize'), 'accepted')
        saved = json.loads((self.data/'automation_state.json').read_text())
        self.assertTrue(saved['inflight'])
        self.assertEqual(saved['next_allowed'], self.now+900)
        self.assertEqual(self.control.accept('optimize'), 'busy')
        first = self.control.take()
        self.assertTrue(first['request_id'].startswith('ha-'))
        self.assertEqual(self.control.accept('optimize'), 'busy')
        self.assertIsNone(self.control.take())
        self.control.finish({'state': 'optimization_not_needed', 'request_id': first['request_id']})
        self.assertEqual(self.control.accept('optimize'), 'request_cooldown')

    def test_no_replay_after_restart_and_cooldown_survives_negative_result(self):
        self.control.accept('optimize')
        new = self.make()
        self.healthy(new)
        self.assertIsNone(new.pending)
        self.assertEqual(new.view()['last_request'], 'interrupted_no_replay')
        self.assertEqual(new.accept('optimize'), 'request_cooldown')
        self.now += 901
        self.healthy(new)
        self.assertEqual(new.accept('optimize'), 'accepted')

    def test_expired_or_unhealthy_pending_command_does_not_execute(self):
        self.control.accept('optimize')
        self.now += 61
        self.healthy()
        self.assertIsNone(self.control.take())
        self.assertEqual(self.control.view()['last_request'], 'request_expired')
        self.assertFalse(self.control.busy)

    def test_storage_failure_blocks_dispatch(self):
        self.control.save = lambda *a: (_ for _ in ()).throw(OSError('PRIVATE'))
        self.assertEqual(self.control.accept('optimize'), 'automation_storage_error')
        self.assertIsNone(self.control.pending)
        self.assertNotIn('PRIVATE', str(self.control.view()))

    def test_invalid_state_is_fail_closed(self):
        (self.data/'automation_state.json').write_text('{broken')
        control = self.make()
        self.healthy(control)
        self.assertEqual(control.accept('optimize'), 'invalid_automation_state')
        self.assertIsNone(control.pending)

    def test_modes_availability_and_stale_monitor_block_commands(self):
        for options, reason in ((dict(self.options, mqtt_commands_enabled=False), 'commands_disabled'),
                                (dict(self.options, tmp_enabled=False), 'commands_disabled'),
                                (dict(self.options, tmp_scan_backend='legacy'), 'invalid_optimize_configuration')):
            control = self.make(options)
            self.healthy(control)
            self.assertEqual(control.accept('optimize'), reason)
        self.now += 61
        self.control.set_monitor({}, False)
        self.assertTrue(self.control.view()['commands_available'])
        self.assertEqual(self.control.accept('optimize'), 'accepted')
        self.assertIsNotNone(self.control.take())

    def test_tmp_auth_guard_blocks_admission_without_consuming_a_request(self):
        run.save(self.data/'tmp_state.json', {'next_login': self.now + 900})
        self.assertEqual(self.control.accept('optimize'), 'tmp_auth_cooldown')
        self.assertIsNone(self.control.pending)
        self.assertEqual(self.control.control['next_allowed'], 0)
        (self.data/'tmp_state.json').write_text('{"next_login": NaN}')
        self.assertEqual(self.control.accept('optimize'), 'invalid_tmp_state')

    def test_pending_apply_requires_verification_even_after_admission_cooldown(self):
        target = m9_actions.target_hash(self.options)
        report = {'request_id': 'old', 'state': 'apply_dispatch_uncertain', 'started_at': 100,
                  'apply_command_attempted': True, 'apply_attempted_at': 100,
                  'channels_before_apply': {'state': 'available', 'data': {'band5_2': {'channel': 149}}}}
        run.save(self.data/'tmp_optimize_journal.json', {'version': 1, 'actions': {'old': {'target_hash': target, 'report': report}}})
        self.assertEqual(self.control.accept('optimize'), 'previous_apply_unverified')
        self.assertEqual(self.control.accept('verify'), 'accepted')
        self.assertEqual(self.control.take()['request_id'], 'old')
        self.assertEqual(self.control.view()['state'], 'verifying')

    def test_monitoring_reads_channels_without_starting_acs_or_tmp(self):
        calls = []
        client = SimpleNamespace(get_device_list=lambda: [SimpleNamespace(device_model='M9Plus', software_ver='public')],
                                 get_device_mode=lambda: SimpleNamespace(workmode='FAP'))
        def read_probe(client, name):
            calls.append(name)
            return {'band2_4': {'channel': 9}}
        with patch.object(run, 'probe', side_effect=read_probe), patch.object(tmp_readonly, 'run_probe') as active:
            status = run.collect(client, dict(self.options, diagnose_acs=True), 1, set(), {}, {})
        self.assertEqual(calls, ['channels'])
        self.assertEqual(status['diagnostics']['channels']['data']['band2_4']['channel'], 9)
        active.assert_not_called()

    def test_worker_uses_generated_id_and_never_changes_manual_options(self):
        self.options['tmp_optimize_request_id'] = 'manual-old'
        self.control.accept('optimize')
        client = object()
        with patch.object(tmp_readonly, 'run_probe', return_value={'state': 'optimization_not_needed'}) as probe:
            result = run.process_job(self.control, client, self.options, self.data, stop=threading.Event())
        sent_options = probe.call_args.args[0]
        self.assertEqual(sent_options['tmp_action'], 'optimize')
        self.assertNotEqual(sent_options['tmp_optimize_request_id'], 'manual-old')
        self.assertEqual(self.options['tmp_optimize_request_id'], 'manual-old')
        self.assertFalse(self.control.busy)
        self.assertEqual(result['state'], 'optimization_not_needed')

    def test_public_state_filters_options_and_selects_newer_channel_observation(self):
        self.options.update(password='PRIVATE', tmp_email='PRIVATE', mqtt_password='PRIVATE')
        self.control.set_monitor({'nodes': [], 'mode': 'FAP', 'diagnostics': {
            'channels': {'state': 'available', 'updated_at': 100, 'data': {'band5_2': {'channel': 149}}},
            'performance': {'updated_at': 100, 'data': {'cpu_usage': 0.1, 'mem_usage': 0.32, 'password': 'PRIVATE'}}}}, True)
        self.control.progress({'state': 'apply_acknowledged', 'request_id': 'public', 'password': 'PRIVATE',
                               'verification': {'last_available': {'state': 'available', 'updated_at': 200, 'data': {'band5_2': {'channel': 157}}}}})
        view = self.control.view()
        self.assertEqual(view['band5_2'], 157)
        self.assertEqual(view['cpu_usage'], 10)
        self.assertNotIn('PRIVATE', json.dumps(view))

    def test_worker_failure_is_not_retried(self):
        self.control.accept('optimize')
        with patch.object(tmp_readonly, 'run_probe', side_effect=RuntimeError('PRIVATE')) as probe:
            result = run.process_job(self.control, object(), self.options, self.data)
            self.assertIsNone(run.process_job(self.control, object(), self.options, self.data))
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(result, {'state': 'action_failed_no_replay'})
        self.assertFalse(self.control.view()['ready'])


if __name__ == '__main__': unittest.main()
