"""Exact M5 pilot profiles; no inference from firmware or satellite revisions."""
import json

FIRMWARE = '1.9.5 Build 20260602 Rel. 25369'
HARDWARE = frozenset(('3.0', '3.2'))


def supported(model, hardware, firmware, mode):
    return (model == 'M5' and isinstance(hardware, str) and hardware in HARDWARE
            and firmware == FIRMWARE and mode in ('AP', 'FAP'))


def supported_snapshot(snapshot):
    nodes = snapshot.get('nodes')
    if not isinstance(nodes, list):
        return False
    masters = [node for node in nodes if isinstance(node, dict) and node.get('role') == 'master']
    if len(masters) != 1:
        return False
    node = masters[0]
    return supported(node.get('model'), node.get('hardware'), node.get('firmware'), snapshot.get('mode'))


def read_supported(reader):
    """Read only inventory and mode through a dedicated metadata whitelist."""
    try:
        documents = []
        for opcode in (0x400F, 0x40A0):
            document = json.loads(reader.profile_request(opcode))
            if (not isinstance(document, dict) or type(document.get('error_code')) is not int
                    or document['error_code'] != 0 or not isinstance(document.get('result'), dict)):
                return False
            documents.append(document['result'])
        devices = documents[0].get('device_list')
        if not isinstance(devices, list):
            return False
        masters = [node for node in devices if isinstance(node, dict) and node.get('role') == 'master']
        if len(masters) != 1:
            return False
        node = masters[0]
        return supported(node.get('device_model'), node.get('hardware_ver'),
                         node.get('software_ver'), documents[1].get('mode'))
    except Exception:
        return False
