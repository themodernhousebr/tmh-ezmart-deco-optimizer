"""Runtime projections, node identity, freshness and independent IP observations."""
import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ezmart_deco_optimizer'))
import diagnostics
import mesh
import ip_monitor
import mqtt_bridge
import run
import x50_tpap as protocol
from ha_control import Controller


def devices():
    return [dict(mac='AA:BB:CC:00:00:01', device_id='a'*32, device_model='M5',
                 software_ver='1.9.5 Build 20260602 Rel. 25369', hardware_ver='3.2', role='master',
                 nickname='custom', custom_nickname=base64.b64encode('Sala'.encode()).decode(),
                 group_status='connected', inet_status='offline', ip='192.0.2.1'),
            dict(mac='AA-BB-CC-00-00-02', device_id='b'*32, device_model='M5', role='slave',
                 nickname='Quarto', group_status='disconnected', ip='192.0.2.2',
                 parent_device_id='a'*32, connection_type=['wired', 'PRIVATE'],
                 signal_level={'band2_4': 0, 'secret': 'PRIVATE'}, password='PRIVATE')]


class MeshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)

    def test_identity_survives_mac_format_name_ip_and_inventory_order_changes(self):
        raw = devices()
        first = mesh.project(raw)
        raw[0].update(mac='aa-bb-cc-00-00-01', ip='192.0.2.99',
                      custom_nickname=base64.b64encode('Escritório'.encode()).decode())
        second = mesh.project(list(reversed(raw)))
        self.assertEqual(first[0]['id'], second[1]['id'])
        self.assertEqual(second[1]['name'], 'Escritório')
        self.assertEqual(second[1]['ip'], '192.0.2.99')
        self.assertTrue(first[0]['connected'])  # WAN failure does not mean mesh failure.
        self.assertFalse(first[1]['connected'])
        self.assertEqual(first[1]['parent_id'], first[0]['id'])
        self.assertNotIn('PRIVATE', json.dumps(first))
        self.assertNotIn('AA:BB', json.dumps(first))

    def test_unknown_is_not_connected_and_no_id_is_not_discovered(self):
        raw = devices()
        raw[0].pop('group_status')
        raw[1].update(mac='invalid', device_id='invalid')
        result = mesh.project(raw)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]['connected'])
        for bad in (None, {}, [{}]*65):
            with self.assertRaises(ValueError):
                mesh.project(bad)

    def test_registry_preserves_disappeared_node_but_never_persists_on(self):
        registry = mesh.Registry(self.data/'mesh.json', run.save)
        first = mesh.project(devices())
        self.assertTrue(registry.update(first))
        registry.update(first[:1])
        restored = mesh.Registry(self.data/'mesh.json', run.save)
        self.assertEqual(len(restored.nodes), 2)
        self.assertNotIn('connected', (self.data/'mesh.json').read_text())

    def test_discovery_entity_ids_fixed_device_name_updates(self):
        node = mesh.project(devices())[0]
        for kind in ('mesh', 'ip', 'ip_response'):
            topic, old = mesh.discovery('installation', 'base', node, 360, kind)
            node = dict(node, name='Novo nome')
            new_topic, new = mesh.discovery('installation', 'base', node, 360, kind)
            self.assertEqual(topic, new_topic)
            self.assertEqual(old['unique_id'], new['unique_id'])
            self.assertEqual(old['default_entity_id'], new['default_entity_id'])
            self.assertEqual(new['device']['name'], 'Novo nome')
            self.assertNotIn('command_topic', new)

    def test_http_inventory_single_request_with_optional_performance(self):
        client = Mock()
        def request(path, form, params):
            self.assertEqual(params, {'operation': 'read'})
            if form == 'device_list': return {'device_list': devices()}
            if form == 'performance': return {'cpu_usage': .24, 'mem_usage': .32, 'secret': 'PRIVATE'}
            return {}
        client.request.side_effect = request
        client.get_device_mode.return_value = SimpleNamespace(workmode='FAP', sysmode='Router')
        status = run.collect(client, dict(deco_model='m5', monitor_nodes=True, monitor_performance=True), 1, set(), {}, {})
        client.get_device_list.assert_not_called()
        self.assertEqual(len(status['mesh_nodes']), 2)
        self.assertEqual(status['diagnostics']['performance']['data'], {'cpu_usage': .24, 'mem_usage': .32})
        self.assertNotIn('PRIVATE', json.dumps(status))
        self.assertEqual(sum(call.args[1] == 'device_list' for call in client.request.call_args_list), 1)

    def test_http_optional_failure_preserves_inventory_and_main_monitor(self):
        client = Mock()
        client.get_device_mode.return_value = SimpleNamespace(workmode='FAP', sysmode='Router')
        client.request.side_effect = lambda path, form, args: ({'device_list': devices()} if form == 'device_list'
            else (_ for _ in ()).throw(TimeoutError()))
        status = run.collect(client, dict(deco_model='m5', monitor_nodes=True, monitor_performance=True), 1, set(), {}, {})
        self.assertTrue(status['available'])
        self.assertEqual(status['diagnostics']['performance']['state'], 'error')

    def test_publish_lost_inventory_unknown_but_ip_result_stays_independent(self):
        stop = threading.Event()
        bridge = mqtt_bridge.Bridge({'poll_seconds':300}, self.data, run.save, Mock(), stop, 'test')
        client = Mock()
        nodes = mesh.project(devices())
        bridge.publish_mesh(client, {'mesh_nodes':nodes, 'mesh_at':100, 'mesh_fresh':True})
        with bridge.ip_monitor.lock:
            bridge.ip_monitor.results[nodes[0]['id']] = {'ip': nodes[0]['ip'], 'connected': True, 'checked_at':time.time()}
        bridge.publish_mesh(client, {'mesh_nodes':[], 'mesh_at':0, 'mesh_fresh':False})
        states = [json.loads(c.args[1]) for c in client.publish.call_args_list if c.args[0].endswith('/state')]
        last = [s for s in states if s['id'] == nodes[0]['id']][-1]
        self.assertIsNone(last['connected'])
        self.assertTrue(last['ip_connected'])
        for call in client.publish.call_args_list:
            if call.args[0].endswith('/state'):
                self.assertFalse(call.kwargs['retain'])
        self.assertEqual(len(bridge.mesh_registry.nodes), 2)

    def test_freshness_expires_without_fabricating_offline(self):
        options = dict(deco_model='m5', poll_seconds=300)
        clock = [1000]
        control = Controller(options, self.data, run.save, clock=lambda: clock[0])
        control.set_monitor({'mesh_nodes':mesh.project(devices()), 'mesh_at':1000,
            'diagnostics': {'performance':{'state':'available','updated_at':1000,'data':{'cpu_usage':.4}}}}, True)
        self.assertTrue(control.view()['mesh_fresh'])
        self.assertEqual(control.view()['cpu_usage'], 40)
        clock[0] += 361
        self.assertFalse(control.view()['mesh_fresh'])
        self.assertIsNone(control.view()['cpu_usage'])


class PerformanceTests(unittest.TestCase):
    def test_resource_units_and_invalid_values(self):
        document = {'result': {'device_list':[{'cpu_usage':24,'memory_usage':128,'memory_total':512,'password':'PRIVATE'}]}}
        self.assertEqual(diagnostics.resource_performance(document), {'cpu_usage':.24, 'mem_usage':.25})
        for cpu in (True, -1, 101, float('nan'), '24'):
            document['result']['device_list'][0]['cpu_usage'] = cpu
            self.assertNotIn('cpu_usage', diagnostics.resource_performance(document))
        document['result']['device_list'][0].update(memory_usage=513)
        self.assertNotIn('mem_usage', diagnostics.resource_performance(document))

    def test_resource_gate_cannot_enable_writes_or_acs(self):
        channel = SimpleNamespace(send=Mock(), report={})
        reader = protocol.TMPReader(channel)
        reader.transaction = 1
        with self.assertRaises(protocol.ProtocolError): reader.send(0x4317, b'{}')
        reader.allow_resource = True
        reader.send(0x4317, b'{}')
        for opcode in (0x4008, protocol.ACS_SCAN, protocol.ACS_APPLY, 0x4318):
            with self.assertRaises(protocol.ProtocolError): reader.send(opcode, b'{}')
        self.assertEqual(channel.send.call_count, 1)

    def test_optional_x50_read_failure_does_not_break_authenticated_inventory(self):
        bodies = {1: b'a'*32}
        for name, opcode in protocol.READ_OPS.items():
            if name == 'mode': data = {'mode':'Router'}
            elif name == 'devices': data = {'device_list':devices()}
            else: data = {'band2_4':{'backhaul':{'channel':6}}}
            bodies[opcode] = json.dumps({'error_code':0,'result':data}).encode()
        reader = Mock()
        reader.request.side_effect = lambda op, timeout: bodies[op] if op in bodies else (_ for _ in ()).throw(TimeoutError())
        result = {}
        with patch.object(protocol, 'TMPChannel'), patch.object(protocol, 'TMPReader', return_value=reader):
            protocol.read_information(None, None, 10, result, include_nodes=True, include_performance=True)
        self.assertEqual(result['state'], 'readonly_verified')
        self.assertEqual(result['performance']['state'], 'unavailable')
        self.assertEqual(len(result['mesh_nodes']), 2)
        self.assertFalse(reader.allow_resource)
        self.assertEqual([c.args[0] for c in reader.request.call_args_list], [1, *protocol.READ_OPS.values(), 0x4317])


class IPTests(unittest.TestCase):
    def test_literal_ip_checks_bounded_and_no_shell(self):
        for code, expected in ((0, True), (1, False), (2, None)):
            with patch.object(subprocess, 'run', return_value=SimpleNamespace(returncode=code)) as call:
                self.assertIs(ip_monitor.ping('192.0.2.1'), expected)
                self.assertEqual(call.call_args.args[0][-1], '192.0.2.1')
                self.assertNotIn('shell', call.call_args.kwargs)
        with patch.object(subprocess, 'run') as call:
            for invalid in ('example.com', '192.0.2.1;reboot', '127.0.0.1', '0.0.0.0', '224.0.0.1'):
                self.assertIsNone(ip_monitor.ping(invalid))
            call.assert_not_called()
        with patch.object(subprocess, 'run', side_effect=FileNotFoundError()):
            self.assertIsNone(ip_monitor.ping('192.0.2.1'))

    def test_ip_change_discards_old_observation_and_checks_all_nodes(self):
        monitor = ip_monitor.Monitor(threading.Event(), 300, check=lambda ip: ip.endswith('.1'), clock=lambda:1000)
        monitor.update({'a':'192.0.2.1','b':'192.0.2.2'})
        monitor.cycle()
        self.assertTrue(monitor.snapshot()['a']['connected'])
        self.assertFalse(monitor.snapshot()['b']['connected'])
        monitor.update({'a':'192.0.2.3'})
        self.assertEqual(monitor.snapshot(), {})
        monitor.cycle()
        self.assertFalse(monitor.snapshot()['a']['connected'])
