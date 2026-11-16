"""Bounded projections of node metadata, never WLAN/account documents."""
import base64
import hashlib
import ipaddress
import re


# Built-in location identifiers returned by Deco firmware, localized by its app.
PRESET_NAMES = {
    'bedroom': 'Quarto', 'hallway': 'Corredor', 'kitchen': 'Cozinha',
    'living room': 'Sala de estar', 'master bedroom': 'Quarto principal',
    'main bedroom': 'Quarto principal', 'office': 'Escritório',
    'study': 'Sala de estudos', 'garden': 'Jardim', 'garage': 'Garagem',
    'front door': 'Entrada', 'back yard': 'Quintal',
}


def device_name(raw, fallback):
    """Custom labels win verbatim; translate only recognized built-in presets."""
    custom = raw.get('custom_nickname')
    try:
        custom = base64.b64decode(custom, validate=True).decode('utf-8') if isinstance(custom, str) and len(custom) <= 512 else None
    except (ValueError, UnicodeError):
        custom = None
    if text(custom):
        return text(custom), 'custom'
    reported = text(raw.get('nickname'))
    if reported:
        preset = PRESET_NAMES.get(reported.casefold().replace('_', ' '))
        return (preset, 'preset') if preset else (reported, 'reported')
    return fallback, 'fallback'


def text(value):
    return value.strip() if isinstance(value, str) and 0 < len(value.strip()) <= 128 and all(ord(c) >= 32 for c in value) else None


def reference(value):
    if not isinstance(value, str):
        return None
    if re.fullmatch(r'(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}', value):
        return value.replace(':', '').replace('-', '').lower()
    if re.fullmatch(r'[0-9a-fA-F]{12,64}', value):
        return value.lower()
    return None


def identity(value):
    value = reference(value)
    return hashlib.sha256(value.encode()).hexdigest()[:24] if value else None


def project(devices):
    if not isinstance(devices, list) or len(devices) > 64:
        raise ValueError('invalid_mesh_inventory')
    aliases = {}
    for raw in devices:
        if not isinstance(raw, dict):
            continue
        key = identity(raw.get('mac')) or identity(raw.get('device_id'))
        if key:
            for field in ('mac', 'device_id'):
                ref = reference(raw.get(field))
                if ref:
                    aliases[ref] = key
    nodes = []
    seen = set()
    for raw in devices:
        if not isinstance(raw, dict):
            continue
        key = identity(raw.get('mac')) or identity(raw.get('device_id'))
        if not key or key in seen:
            continue
        seen.add(key)
        node = {'id': key}
        for source, target in (('device_model', 'model'), ('hardware_ver', 'hardware'), ('software_ver', 'firmware')):
            value = text(raw.get(source))
            if value:
                node[target] = value
        node['name'], node['name_source'] = device_name(raw, 'Deco ' + key[-6:])
        if raw.get('role') in ('master', 'slave'):
            node['role'] = raw['role']
        try:
            address = ipaddress.ip_address(raw.get('device_ip') or raw.get('ip'))
            if not address.is_multicast and not address.is_unspecified:
                node['ip'] = str(address)
        except (ValueError, TypeError):
            pass
        # inet_status is WAN connectivity: never use it to classify a satellite.
        group = raw.get('group_status')
        node['connected'] = True if group == 'connected' else False if group == 'disconnected' else None
        node['connection_source'] = 'deco_mesh'
        connections = raw.get('connection_type', [])
        if isinstance(connections, str):
            connections = [connections]
        if isinstance(connections, list):
            node['connection_type'] = [v for v in connections if v in ('wired', 'wireless', 'plc', 'band2_4', 'band5', 'band5_1', 'band5_2', 'band6')]
        parent = aliases.get(reference(raw.get('parent_device_id'))) or aliases.get(reference(raw.get('previous')))
        if parent and parent != key:
            node['parent_id'] = parent
        signal = raw.get('signal_level', {})
        if isinstance(signal, dict):
            node['signal_level'] = {k: v for k, v in signal.items() if k in ('band2_4', 'band5', 'band5_1', 'band5_2', 'band6') and type(v) is int and 0 <= v <= 5}
        nodes.append(node)
    return nodes


class Registry:
    """Remember discovered identities across restart, without replaying stale states."""
    def __init__(self, path, save):
        import json
        self.path, self.save = path, save
        stored = json.loads(path.read_text()) if path.exists() else {}
        self.nodes = stored.get('nodes', {})
        if (not isinstance(self.nodes, dict) or len(self.nodes) > 256
                or any(not re.fullmatch('[0-9a-f]{24}', k) or not isinstance(v, dict) for k, v in self.nodes.items())):
            raise ValueError('invalid_mesh_registry')

    def update(self, current):
        changed = False
        for node in current[:64]:
            key = node.get('id')
            if not isinstance(key, str) or not re.fullmatch('[0-9a-f]{24}', key):
                continue
            # Persist only descriptive metadata, never connectivity observations.
            details = {k: v for k, v in node.items() if k in ('id', 'name', 'model', 'hardware', 'firmware', 'role', 'ip')}
            merged = dict(self.nodes.get(key, {}), **details)
            if merged != self.nodes.get(key) and (key in self.nodes or len(self.nodes) < 256):
                self.nodes[key] = merged
                changed = True
        if changed:
            self.save(self.path, {'version': 1, 'nodes': self.nodes})
        return changed


def discovery(identity, base, node, expiry, kind='mesh'):
    key = node['id']
    unique = 'ezmart_deco_' + identity + '_node_' + key
    topic = base + '/nodes/' + key
    device = {'identifiers': [unique], 'name': node.get('name', 'Deco ' + key[-6:]),
              'manufacturer': 'TP-Link', 'model': node.get('model', 'Deco'),
              'via_device': 'ezmart_deco_' + identity}
    if node.get('firmware'):
        device['sw_version'] = node['firmware']
    if node.get('hardware'):
        device['hw_version'] = node['hardware']
    config = {'name': 'Conectividade', 'unique_id': unique + '_connectivity',
              'default_entity_id': 'binary_sensor.ezmart_deco_no_' + key[-8:] + '_conectividade',
              'device': device, 'device_class': 'connectivity',
              'availability_topic': base + '/availability', 'state_topic': topic + '/state',
              'expire_after': expiry, 'payload_on': 'ON', 'payload_off': 'OFF',
              'value_template': "{{ 'None' if value_json.connected is none else ('ON' if value_json.connected else 'OFF') }}",
              'json_attributes_topic': topic + '/state'}
    suffix = '_connectivity'
    domain = 'binary_sensor'
    if kind == 'ip_response':
        suffix = '_ip_response'
        config.update(name='Resposta IP', value_template="{{ 'None' if value_json.ip_connected is none else ('ON' if value_json.ip_connected else 'OFF') }}")
    elif kind == 'ip':
        suffix = '_ip'
        domain = 'sensor'
        config.update(name='Endereço IP', icon='mdi:ip-network', entity_category='diagnostic',
                      value_template='{{ value_json.ip | default(none) }}')
        for field in ('device_class', 'payload_on', 'payload_off', 'expire_after'):
            config.pop(field, None)
    config['unique_id'] = unique + suffix
    config['default_entity_id'] = domain + '.ezmart_deco_no_' + key[-8:] + suffix
    return 'homeassistant/' + domain + '/' + unique + suffix + '/config', config
