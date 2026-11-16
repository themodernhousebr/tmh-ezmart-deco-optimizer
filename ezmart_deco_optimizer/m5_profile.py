"""Exact M5 pilot profiles; no inference from firmware or satellite revisions."""
import json
import re

FIRMWARE = '1.9.5 Build 20260602 Rel. 25369'
HARDWARE = frozenset(('3.0', '3.2'))


def supported(model, hardware, firmware, mode):
    return (model == 'M5' and isinstance(hardware, str) and hardware in HARDWARE
            and firmware == FIRMWARE and mode in ('Router', 'AP'))


def supported_snapshot(snapshot):
    nodes = snapshot.get('nodes')
    if not isinstance(nodes, list):
        return False
    masters = [node for node in nodes if isinstance(node, dict) and node.get('role') == 'master']
    if len(masters) != 1:
        return False
    node = masters[0]
    return supported(node.get('model'), node.get('hardware'), node.get('firmware'), snapshot.get('mode'))


def public_node(node):
    out = {}
    for key, pattern in (('device_model', r'(?:M5|M9Plus|X50)'),
                         ('hardware_ver', r'[0-9]{1,2}(?:\.[0-9]{1,2}){0,2}'),
                         ('software_ver', r'[0-9.]{3,20}(?: Build [0-9]{6,10} Rel[. ]{0,2}[0-9]{1,6})?')):
        value = node.get(key)
        if isinstance(value, str) and re.fullmatch(pattern, value):
            out[key] = value
    return out


def read_supported(reader, diagnostic=None):
    """Read only inventory and mode through a dedicated metadata whitelist."""
    diagnostic = diagnostic if diagnostic is not None else {}
    try:
        documents = []
        for opcode in (0x400F, 0x40A0):
            diagnostic.update(state='reading', stage='devices' if opcode == 0x400F else 'mode')
            document = json.loads(reader.profile_request(opcode))
            if (not isinstance(document, dict) or type(document.get('error_code')) is not int
                    or document['error_code'] != 0 or not isinstance(document.get('result'), dict)):
                diagnostic['state'] = 'api_or_shape_error'
                if isinstance(document, dict) and type(document.get('error_code')) is int:
                    diagnostic['api_code'] = document['error_code']
                return False
            documents.append(document['result'])
        devices = documents[0].get('device_list')
        if not isinstance(devices, list):
            diagnostic['state'] = 'device_list_missing'
            return False
        masters = [node for node in devices if isinstance(node, dict) and node.get('role') == 'master']
        diagnostic.update(node_count=len(devices), master_count=len(masters))
        if len(masters) != 1:
            diagnostic['state'] = 'master_not_unique'
            return False
        node = masters[0]
        diagnostic['master'] = public_node(node)
        mode = documents[1].get('mode')
        diagnostic['mode'] = mode if mode in ('AP', 'FAP', 'Router', 'HAP', 'RE') else 'unrecognized'
        accepted = supported(node.get('device_model'), node.get('hardware_ver'),
                             node.get('software_ver'), mode)
        diagnostic['state'] = 'profile_supported' if accepted else 'profile_not_supported'
        return accepted
    except Exception as error:
        diagnostic['state'] = 'profile_read_failed'
        diagnostic['error_type'] = type(error).__name__ if type(error).__name__ in (
            'ProtocolError', 'TimeoutError', 'JSONDecodeError', 'UnicodeDecodeError') else 'unrecognized'
        framing = getattr(reader, 'profile_diagnostic', {})
        if isinstance(framing, dict):
            diagnostic['framing'] = {key: value for key, value in framing.items()
                if key in ('version', 'subversion', 'opcode', 'kind', 'error', 'chunk_bytes',
                           'total_bytes', 'offset', 'chunk_crc_matches', 'assembled_crc_matches')
                and type(value) in (int, bool)}
        return False
