"""Home Assistant discovery; MQTT 5 commands are live, non-retained and bounded."""
import json
import logging
import os
import re
import ssl
import threading
import time
import urllib.request
import uuid

LOG = logging.getLogger('ezmart')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def broker_settings(options):
    if options.get('mqtt_host', '').strip():
        result = {'host': options['mqtt_host'].strip(), 'port': options.get('mqtt_port', 1883),
                  'username': options.get('mqtt_username', ''), 'password': options.get('mqtt_password', ''),
                  'ssl': options.get('mqtt_tls', False)}
    else:
        token = os.environ.get('SUPERVISOR_TOKEN', '')
        if not token:
            raise ValueError()
        request = urllib.request.Request('http://supervisor/services/mqtt', headers={'Authorization': 'Bearer ' + token})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=10) as response:
            document = json.loads(response.read(65537))
        if document.get('result') != 'ok':
            raise ValueError()
        result = document['data']
    if (not isinstance(result.get('host'), str) or not result['host']
            or type(result.get('port')) is not int or not 1 <= result['port'] <= 65535
            or type(result.get('ssl', False)) is not bool
            or not isinstance(result.get('username', ''), str)
            or not isinstance(result.get('password', ''), str)):
        raise ValueError()
    return result


# domain, id suffix, public field, friendly name, extra discovery fields
ENTITIES = (
    ('button', 'otimizar', 'optimize', 'Otimizar se necessário', {'icon': 'mdi:wifi-cog'}),
    ('button', 'verificar', 'verify', 'Verificar última aplicação', {'icon': 'mdi:check-network-outline'}),
    ('binary_sensor', 'pronto_para_otimizar', 'ready', 'Pronto para otimizar', {}),
    ('binary_sensor', 'em_execucao', 'busy', 'Em execução', {'icon': 'mdi:progress-clock'}),
    ('binary_sensor', 'principal_acessivel', 'reachable', 'Principal acessível', {'device_class': 'connectivity'}),
    ('binary_sensor', 'recomendacao', 'need_optimize', 'Última análise recomendou otimizar', {}),
    ('binary_sensor', 'canais_alterados', 'channels_changed', 'Última aplicação mudou canais', {}),
    ('sensor', 'estado', 'state', 'Estado da operação', {}),
    ('sensor', 'resultado', 'result', 'Último resultado', {}),
    ('sensor', 'ultimo_pedido', 'last_request', 'Último pedido recebido', {'entity_category': 'diagnostic'}),
    ('sensor', 'bloqueio', 'blocked_reason', 'Motivo de bloqueio', {'entity_category': 'diagnostic'}),
    ('sensor', 'ultima_acao', 'last_action_at', 'Última ação', {'device_class': 'timestamp'}),
    ('sensor', 'proximo_pedido', 'next_allowed_at', 'Próximo pedido permitido após', {'device_class': 'timestamp'}),
    ('sensor', 'ultimo_monitoramento', 'monitor_at', 'Último monitoramento', {'device_class': 'timestamp'}),
    ('sensor', 'leitura_canais', 'channels_at', 'Horário da leitura de canais', {'device_class': 'timestamp'}),
    ('sensor', 'canal_2_4', 'band2_4', 'Canal 2,4 GHz', {'icon': 'mdi:wifi'}),
    ('sensor', 'canal_5_1', 'band5_1', 'Canal 5 GHz 1', {'icon': 'mdi:wifi'}),
    ('sensor', 'canal_5_2', 'band5_2', 'Canal 5 GHz 2', {'icon': 'mdi:wifi'}),
    ('sensor', 'nos', 'node_count', 'Nós no inventário', {}),
    ('sensor', 'modo', 'mode', 'Modo de operação', {'entity_category': 'diagnostic'}),
    ('sensor', 'cpu', 'cpu_usage', 'Uso de CPU', {'unit_of_measurement': '%', 'entity_category': 'diagnostic'}),
    ('sensor', 'memoria', 'mem_usage', 'Uso de memória', {'unit_of_measurement': '%', 'entity_category': 'diagnostic'}),
)


def discovery(identity, base, version, session, model='m9_plus', guest_enabled=False):
    device = {'identifiers': ['ezmart_deco_' + identity], 'name': 'EZmart Deco Optimizer',
              'manufacturer': 'The Modern House', 'model': 'Deco Optimizer — piloto M9 V2', 'sw_version': version}
    if model == 'x50':
        device['model'] = 'Deco Optimizer — X50 1.30 / 1.8.4'
    elif model == 'm5':
        device['model'] = 'Deco Optimizer — M5 1.9.5 (piloto)'
    messages = []
    for domain, suffix, field, extra_name, extra in ENTITIES:
        if model == 'x50' and suffix.startswith('canal_'):
            extra_name = extra_name.replace('Canal ', 'Canal backhaul ')
        unique = 'ezmart_deco_' + identity + '_' + suffix
        config = {'name': extra_name, 'unique_id': unique, 'default_entity_id': domain + '.ezmart_deco_' + suffix,
                  'device': device, 'availability_topic': base + '/availability', **extra}
        if domain == 'button':
            # Session rotates at every broker connection. Old discovery cannot invoke an old job.
            config.update(command_topic=base + '/command', qos=0, retain=False,
                          payload_press=json.dumps({'action': field, 'session': session}, separators=(',', ':')))
            key = ('ready' if field == 'optimize' else 'verify_ready') if model == 'x50' else 'commands_available'
            config.pop('availability_topic')
            config.update(availability=[{'topic': base + '/availability'},
                                       {'topic': base + '/state', 'value_template': "{{ 'online' if value_json." + key + " else 'offline' }}"}],
                          availability_mode='all')
        else:
            config['state_topic'] = base + '/state'
            if domain == 'binary_sensor':
                config.update(payload_on='ON', payload_off='OFF',
                              value_template="{{ 'None' if value_json." + field + " is none else ('ON' if value_json." + field + " else 'OFF') }}")
            else:
                config['value_template'] = '{{ value_json.' + field + ' }}'
            if suffix == 'resultado':
                config.update(json_attributes_topic=base + '/state', json_attributes_template='{{ value_json.details | tojson }}')
        messages.append(('homeassistant/' + domain + '/' + unique + '/config', config))
    if model in ('m9_plus', 'x50', 'm5') and guest_enabled:
        unique = 'ezmart_deco_' + identity + '_visitantes'
        config = {'name': 'Wi-Fi de visitantes', 'unique_id': unique,
                  'default_entity_id': 'switch.ezmart_deco_visitantes', 'device': device, 'icon': 'mdi:wifi',
                  'command_topic': base + '/command', 'state_topic': base + '/state',
                  'optimistic': False, 'qos': 0, 'retain': False, 'state_on': 'ON', 'state_off': 'OFF',
                  'value_template': "{{ 'None' if value_json.guest_enabled is none else ('ON' if value_json.guest_enabled else 'OFF') }}",
                  'payload_on': json.dumps({'action': 'guest_on', 'session': session}, separators=(',', ':')),
                  'payload_off': json.dumps({'action': 'guest_off', 'session': session}, separators=(',', ':')),
                  'availability_mode': 'all',
                  'availability': [{'topic': base + '/availability'}, {'topic': base + '/state',
                                    'value_template': "{{ 'online' if value_json.guest_available else 'offline' }}"}],
                  'json_attributes_topic': base + '/state',
                  'json_attributes_template': '{{ value_json.guest_details | tojson }}'}
        messages.append(('homeassistant/switch/' + unique + '/config', config))
    return messages


class Bridge:
    def __init__(self, options, data, save, controller, stop, version):
        self.options, self.controller, self.stop, self.version = options, controller, stop, version
        identity_file = data / 'mqtt_identity.json'
        if identity_file.exists():
            self.identity = json.loads(identity_file.read_text())['id']
        else:
            self.identity = uuid.uuid4().hex
            save(identity_file, {'id': self.identity})
        if not isinstance(self.identity, str) or not re.fullmatch('[0-9a-f]{32}', self.identity):
            raise ValueError()
        self.base = 'ezmart/deco/' + self.identity
        self.connected = threading.Event()
        self.refresh = threading.Event()
        self.session = ''
        self.client = None
        self.last_connection_warning = None
        self.last_connection_warning_at = 0
        self.thread = threading.Thread(target=self.run, name='ezmart-mqtt', daemon=True)

    def connection_warning(self, category, code=None):
        # Only fixed categories and numeric protocol codes; never exception text.
        warning = (category, code)
        now = time.monotonic()
        if warning != self.last_connection_warning or now - self.last_connection_warning_at >= 60:
            LOG.warning('MQTT: %s; código=%s. Nenhuma otimização solicitada.', category, code)
            self.last_connection_warning = warning
            self.last_connection_warning_at = now

    def on_connect_fail(self, client, userdata):
        self.connected.clear()
        self.session = ''
        self.connection_warning('conexão TCP/TLS indisponível; conferir endereço, porta e TLS')

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            self.connected.clear()
            self.session = ''
            value = getattr(reason_code, 'value', reason_code)
            code = value if type(value) is int else None
            category = ('autenticação ou autorização recusada pelo broker' if code in (134, 135)
                        else 'conexão recusada pelo broker')
            self.connection_warning(category, code)
            return
        from paho.mqtt.subscribeoptions import SubscribeOptions
        self.session = uuid.uuid4().hex
        # MQTT 5 preserves retain on live deliveries and suppresses retained backlog.
        client.subscribe(self.base + '/command', options=SubscribeOptions(qos=0, retainAsPublished=True, retainHandling=2))
        client.subscribe('homeassistant/status', qos=0)
        self.connected.set()
        self.refresh.set()
        self.last_connection_warning = None
        LOG.info('MQTT conectado; entidades do EZmart publicadas via descoberta.')

    def on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected.clear()
        self.session = ''

    def on_message(self, client, userdata, message):
        if message.topic == 'homeassistant/status':
            if message.payload == b'online':
                self.refresh.set()
            return
        if (message.topic != self.base + '/command' or not self.connected.is_set()
                or message.retain or message.dup or len(message.payload) > 256):
            return
        try:
            value = json.loads(message.payload)
            if (not isinstance(value, dict) or set(value) != {'action', 'session'}
                    or not self.session or value['session'] != self.session
                    or value['action'] not in ('optimize', 'verify', 'guest_on', 'guest_off')):
                return
            self.controller.accept(value['action'])
        except (ValueError, TypeError, UnicodeError):
            return

    def publish_discovery(self, client):
        for topic, value in discovery(self.identity, self.base, self.version, self.session,
                                      self.options.get('deco_model', 'm9_plus'), self.options.get('guest_control_enabled', False)):
            client.publish(topic, json.dumps(value, ensure_ascii=True), qos=1, retain=True)
        if self.options.get('deco_model', 'm9_plus') not in ('m9_plus', 'x50', 'm5') or not self.options.get('guest_control_enabled'):
            # Remove only our optional switch when the feature/model changes.
            client.publish('homeassistant/switch/ezmart_deco_' + self.identity + '_visitantes/config',
                           '', qos=1, retain=True)

    def run(self):
        import paho.mqtt.client as mqtt
        while not self.stop.is_set():
            client = None
            try:
                broker = broker_settings(self.options)
                client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='ezmart-' + self.identity,
                                     protocol=mqtt.MQTTv5)
                self.client = client
                client.max_queued_messages_set(100)
                client.on_connect, client.on_disconnect, client.on_message = self.on_connect, self.on_disconnect, self.on_message
                client.on_connect_fail = self.on_connect_fail
                client.username_pw_set(broker.get('username', ''), broker.get('password', ''))
                if broker.get('ssl', False):
                    client.tls_set_context(ssl.create_default_context())
                client.will_set(self.base + '/availability', 'offline', qos=1, retain=True)
                client.reconnect_delay_set(min_delay=2, max_delay=30)
                client.connect_async(broker['host'], broker['port'], keepalive=30, clean_start=True)
                client.loop_start()
                disconnected_at = time.monotonic()
                last_payload = None
                while not self.stop.wait(1):
                    if not self.connected.is_set():
                        if time.monotonic() - disconnected_at > 60:
                            break  # Re-read service credentials; never replay an action.
                        continue
                    disconnected_at = time.monotonic()
                    refresh = self.refresh.is_set()
                    if refresh:
                        self.refresh.clear()
                        self.publish_discovery(client)
                    payload = json.dumps(self.controller.view(), ensure_ascii=True, allow_nan=False)
                    if refresh or payload != last_payload:
                        client.publish(self.base + '/state', payload, qos=1, retain=True)
                        client.publish(self.base + '/availability', 'online', qos=1, retain=True)
                        last_payload = payload
            except Exception:
                LOG.warning('MQTT indisponível; nova tentativa em 30 segundos. Nenhuma otimização agendada.')
            finally:
                self.connected.clear()
                self.session = ''
                if client is not None:
                    try:
                        info = client.publish(self.base + '/availability', 'offline', qos=1, retain=True)
                        info.wait_for_publish(timeout=2)
                    except Exception:
                        pass
                    try:
                        client.disconnect()
                    except Exception:
                        pass
                    try:
                        client.loop_stop()
                    except Exception:
                        pass
            if self.stop.wait(30):
                break

    def start(self):
        self.thread.start()
