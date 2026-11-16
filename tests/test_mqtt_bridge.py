"""Discovery, command lifetime, credentials and broker callbacks without a network."""
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ezmart_deco_optimizer'))
from mqtt_bridge import Bridge, broker_settings, discovery, ENTITIES
from run import save


class MQTTTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.controller = Mock()
        self.bridge = Bridge({}, Path(self.tmp.name), save, self.controller, threading.Event(), '0.8.0-dev.1')
        self.bridge.session = 'live-session'
        self.bridge.connected.set()

    def send(self, value, **kw):
        msg = SimpleNamespace(topic=self.bridge.base+'/command', payload=json.dumps(value).encode(), retain=False, dup=False)
        for key, item in kw.items(): setattr(msg, key, item)
        self.bridge.on_message(None, None, msg)

    def test_commands_are_exact_live_and_not_retained(self):
        valid = {'action': 'optimize', 'session': 'live-session'}
        for kwargs in ({'retain': True}, {'dup': True}, {'topic': 'other'}, {'payload': b'not-json'}, {'payload': b'x'*257}):
            self.send(valid, **kwargs)
        for invalid in ({'action': 'optimize', 'session': 'old-session'}, {'action': 'shell', 'session': 'live-session'},
                        {'action': 'optimize', 'session': 'live-session', 'payload': 'anything'}, [], 'optimize'):
            self.send(invalid)
        self.controller.accept.assert_not_called()
        self.send(valid)
        self.controller.accept.assert_called_once_with('optimize')

    def test_disconnected_commands_are_dropped_not_queued(self):
        self.bridge.on_disconnect(None, None, None, None, None)
        self.send({'action': 'optimize', 'session': 'live-session'})
        self.controller.accept.assert_not_called()
        self.assertEqual(self.bridge.session, '')

    def test_broker_auth_rejection_is_reported_without_credentials_or_repeated_spam(self):
        reason = SimpleNamespace(value=135, private='PRIVATE')
        with self.assertLogs('ezmart', level='WARNING') as logs:
            self.bridge.on_connect(None, None, None, reason, None)
            self.bridge.on_connect(None, None, None, reason, None)
        self.assertEqual(len(logs.output), 1)
        self.assertIn('autenticação ou autorização recusada', logs.output[0])
        self.assertIn('135', logs.output[0])
        self.assertNotIn('PRIVATE', logs.output[0])
        self.assertFalse(self.bridge.connected.is_set())
        self.assertEqual(self.bridge.session, '')
        self.controller.accept.assert_not_called()

    def test_transport_failure_is_reported_without_dispatching_an_action(self):
        with self.assertLogs('ezmart', level='WARNING') as logs:
            self.bridge.on_connect_fail(None, None)
        self.assertIn('TCP/TLS indisponível', logs.output[0])
        self.assertFalse(self.bridge.connected.is_set())
        self.assertEqual(self.bridge.session, '')
        self.controller.accept.assert_not_called()

    def test_home_assistant_birth_only_refreshes_discovery(self):
        self.send({}, topic='homeassistant/status', payload=b'online', retain=True)
        self.assertTrue(self.bridge.refresh.is_set())
        self.controller.accept.assert_not_called()

    def test_identity_survives_restart_without_duplicate_entities(self):
        new = Bridge({}, Path(self.tmp.name), save, self.controller, threading.Event(), '0.8.0-dev.1')
        self.assertEqual(new.base, self.bridge.base)
        self.assertEqual(new.identity, self.bridge.identity)
        self.assertEqual(new.session, '')

    def test_discovery_groups_entities_and_disables_retained_commands(self):
        messages = discovery(self.bridge.identity, self.bridge.base, '0.8.0-dev.1', 'nonce')
        self.assertEqual(len(messages), len(ENTITIES))
        self.assertEqual(len(set(payload['unique_id'] for _, payload in messages)), len(ENTITIES))
        for topic, payload in messages:
            self.assertEqual(payload['device']['manufacturer'], 'The Modern House')
            if '/button/' in topic:
                self.assertFalse(payload['retain'])
                self.assertEqual(payload['qos'], 0)
                self.assertEqual(json.loads(payload['payload_press'])['session'], 'nonce')
                self.assertEqual(payload['availability_mode'], 'all')
                self.assertIn('commands_available', payload['availability'][1]['value_template'])
            else:
                self.assertEqual(payload['state_topic'], self.bridge.base+'/state')
        self.assertNotIn('password', json.dumps(messages))

    def test_connect_uses_mqtt5_retain_as_published_and_no_retained_backlog(self):
        factory = Mock(side_effect=lambda **kw: kw)
        module = SimpleNamespace(SubscribeOptions=factory)
        client = Mock()
        with patch.dict(sys.modules, {'paho.mqtt.subscribeoptions': module}):
            self.bridge.on_connect(client, None, None, 0, None)
        opts = client.subscribe.call_args_list[0].kwargs['options']
        self.assertEqual(opts, {'qos': 0, 'retainAsPublished': True, 'retainHandling': 2})
        self.assertNotEqual(self.bridge.session, 'live-session')
        self.assertTrue(self.bridge.connected.is_set())
        self.controller.accept.assert_not_called()

    def test_supervisor_mqtt_service_lookup_no_extra_permissions_or_secret_logging(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps({'result': 'ok', 'data': {'host': 'core-mosquitto', 'port': 1883,
            'username': 'app-user', 'password': 'PRIVATE', 'ssl': False}}).encode()
        opener = Mock()
        opener.open.return_value = response
        with patch.dict('os.environ', {'SUPERVISOR_TOKEN': 'TOKEN'}), patch('mqtt_bridge.urllib.request.build_opener', return_value=opener):
            settings = broker_settings({})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'http://supervisor/services/mqtt')
        self.assertEqual(request.get_header('Authorization'), 'Bearer TOKEN')
        self.assertEqual(settings['password'], 'PRIVATE')

    def test_custom_broker_and_invalid_settings(self):
        out = broker_settings({'mqtt_host': 'broker.local', 'mqtt_port': 8883, 'mqtt_tls': True})
        self.assertEqual(out['host'], 'broker.local')
        self.assertTrue(out['ssl'])
        with self.assertRaises(ValueError): broker_settings({'mqtt_host': 'host', 'mqtt_port': True})


if __name__ == '__main__': unittest.main()
