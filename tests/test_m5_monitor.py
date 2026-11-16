"""M5 pilot collects inventory and action gates for exact profiles."""
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ezmart_deco_optimizer'))
import run
import ha_control
import mqtt_bridge
import tmp_readonly
import m9_actions


class M5MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.options = dict(deco_model='m5', host='192.168.68.1', password='PRIVATE',
                            mqtt_enabled=True, tmp_enabled=False, mqtt_commands_enabled=False,
                            guest_control_enabled=False, tmp_scan_backend='m9_acs')
        self.stop = threading.Event()

    def client(self):
        client = MagicMock()
        client.get_device_list.return_value = [SimpleNamespace(device_model='M5',
            software_ver='1.9.5 Build 20260602 Rel. 25369', hardware_ver='3.20', role='master',
            mac='PRIVATE', nickname='PRIVATE')]
        client.get_device_mode.return_value = SimpleNamespace(workmode='FAP', sysmode='Router')
        client.request.return_value = {'band2_4': {'radio': {'channel': 6}},
                                      'band5_1': {'radio': {'channel': 36}}}
        return client

    def test_inventory_projects_hardware_role_without_private_identifiers(self):
        result = run.collect(self.client(), self.options, 1, set(), {}, {})
        self.assertEqual(result['nodes'], [{'model':'M5', 'hardware':'3.20', 'role':'master',
                           'firmware':'1.9.5 Build 20260602 Rel. 25369'}])
        self.assertTrue(result['available'])
        self.assertTrue(result['read_only'])
        self.assertFalse(result['profile_supported'])
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_http_mesh_workmode_does_not_override_router_system_mode(self):
        client = self.client()
        client.get_device_list.return_value[0].hardware_ver = '3.2'
        result = run.collect(client, self.options, 1, set(), {}, {})
        self.assertEqual(result['workmode'], 'FAP')
        self.assertEqual(result['mode'], 'Router')
        self.assertTrue(result['profile_supported'])

    def test_missing_or_unknown_system_mode_never_falls_back_to_fap(self):
        for value in (None, 'PRIVATE'):
            client = self.client()
            client.get_device_list.return_value[0].hardware_ver = '3.2'
            client.get_device_mode.return_value = SimpleNamespace(workmode='FAP', sysmode=value)
            result = run.collect(client, self.options, 1, set(), {}, {})
            self.assertEqual(result['mode'], 'unknown')
            self.assertFalse(result['profile_supported'])
            self.assertNotIn('PRIVATE', json.dumps(result))

    def test_invalid_hardware_and_role_are_omitted(self):
        client = self.client()
        node = client.get_device_list.return_value[0]
        node.hardware_ver = 'PRIVATE'
        node.role = 'PRIVATE'
        result = run.collect(client, self.options, 1, set(), {}, {})
        self.assertNotIn('hardware', result['nodes'][0])
        self.assertNotIn('role', result['nodes'][0])

    def test_controller_and_transport_reject_actions_even_if_options_enabled(self):
        options = dict(self.options, tmp_enabled=True, mqtt_commands_enabled=True, guest_control_enabled=True)
        control = ha_control.Controller(options, self.data, run.save)
        control.set_monitor({'nodes':[], 'mode':'Router'}, True)
        for action in ('optimize', 'verify'):
            self.assertEqual(control.accept(action), 'm5_profile_not_validated')
        self.assertEqual(control.accept('guest_on'), 'm5_profile_not_validated')
        self.assertFalse(control.view()['commands_available'])
        self.assertFalse(control.view()['guest_available'])
        with patch.object(tmp_readonly.socket, 'create_connection') as connect:
            for action in ('read', 'scan'):
                result = tmp_readonly.run_probe(dict(self.options, tmp_action=action), self.data, run.save)
                self.assertEqual(result['state'], 'm5_action_not_supported')
            connect.assert_not_called()
        self.assertFalse(run.startup_action_enabled(dict(self.options, mqtt_enabled=False)))

    def test_m5_runtime_reads_http_only_and_defaults_to_five_minutes(self):
        run.save(self.data/'options.json', self.options)
        sdk = SimpleNamespace(DecoClient=MagicMock(return_value=self.client()))
        save = run.save
        def store(path, value):
            save(path, value)
            if path.name == 'status.json':
                self.stop.set()
        with patch.dict(sys.modules, {'tplink_deco_api':sdk}), \
                patch.object(run, 'Path', return_value=self.data), patch.object(run, 'STOP', self.stop), \
                patch.object(run, 'save', side_effect=store), patch.object(mqtt_bridge, 'Bridge') as bridge, \
                patch.object(tmp_readonly, 'run_probe') as action:
            self.assertEqual(run.main(), 0)
        action.assert_not_called()
        sdk.DecoClient.assert_called_once_with('192.168.68.1', 'admin', 'PRIVATE', timeout=10)
        status = json.loads((self.data/'status.json').read_text())
        self.assertEqual(status['nodes'][0]['hardware'], '3.20')
        self.assertEqual(bridge.call_args.args[0]['poll_seconds'], 300)


if __name__ == '__main__':
    unittest.main()
