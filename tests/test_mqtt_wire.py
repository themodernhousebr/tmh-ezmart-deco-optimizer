"""Optional MQTT 5 loopback test using the real Paho client, never a real Deco."""
import importlib.util
import json
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ezmart_deco_optimizer'))
from ha_control import Controller
from mqtt_bridge import Bridge, ENTITIES
from run import save


def variable(value):
    result = bytearray()
    while True:
        digit, value = value % 128, value // 128
        result.append(digit | (128 if value else 0))
        if not value: return bytes(result)


def exact(sock, count):
    out = b''
    while len(out) < count:
        part = sock.recv(count-len(out))
        if not part: raise EOFError()
        out += part
    return out


def frame(sock):
    header = exact(sock, 1)[0]
    size, multiplier = 0, 1
    for _ in range(4):
        digit = exact(sock, 1)[0]
        size += (digit & 127) * multiplier
        if digit < 128: return header, exact(sock, size)
        multiplier *= 128
    raise ValueError()


@unittest.skipUnless(importlib.util.find_spec('paho'), 'Paho optional; isolated wheel required for loopback validation')
class WireTests(unittest.TestCase):
    def test_real_client_discovery_and_retained_stale_duplicate_command_guards(self):
        self.run_wire()

    def test_real_guest_switch_discovery_and_command_guards(self):
        self.run_wire(guest_mode=True)

    def run_wire(self, guest_mode=False):
        stop = threading.Event()
        server = socket.socket()
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        server.settimeout(5)
        messages, subscriptions, errors = [], [], []
        peers, write_lock = [], threading.Lock()
        def send(packet):
            with write_lock: peers[0].sendall(packet)
        def broker():
            try:
                peer, _ = server.accept()
                peers.append(peer)
                peer.settimeout(8)
                while not stop.is_set():
                    header, body = frame(peer)
                    kind = header >> 4
                    if kind == 1:
                        self.assertEqual(body[6], 5)  # MQTT protocol level 5.
                        self.assertTrue(body[7] & 2)  # Clean start.
                        send(b'\x20\x03\x00\x00\x00')
                    elif kind == 8:
                        length = int.from_bytes(body[3:5], 'big')  # Empty properties.
                        topic = body[5:5+length].decode()
                        subscriptions.append((topic, body[5+length]))
                        send(b'\x90\x04' + body[:2] + b'\x00\x00')
                    elif kind == 3:
                        length = int.from_bytes(body[:2], 'big')
                        topic = body[2:2+length].decode()
                        index = 2+length
                        if (header >> 1) & 3:
                            packet_id = body[index:index+2]
                            index += 2
                            send(b'\x40\x02' + packet_id)
                        self.assertEqual(body[index], 0)  # Empty properties.
                        messages.append((topic, body[index+1:]))
                    elif kind == 12:
                        send(b'\xd0\x00')
                    elif kind == 14:
                        return
            except (EOFError, OSError):
                pass
            except Exception as error:
                errors.append(type(error).__name__)
        thread = threading.Thread(target=broker, daemon=True)
        thread.start()
        with tempfile.TemporaryDirectory(prefix='ezmart-wire-', dir=tempfile.gettempdir()) as directory:
            data = Path(directory)
            options = {'host': '192.168.68.4', 'tmp_host_key_sha256': 'a'*64, 'tmp_scan_backend': 'm9_acs',
                       'mqtt_commands_enabled': True, 'tmp_enabled': True, 'mqtt_host': '127.0.0.1',
                       'mqtt_port': server.getsockname()[1], 'mqtt_enabled': True,
                       'guest_control_enabled': guest_mode}
            controller = Controller(options, data, save)
            controller.set_monitor({'nodes': [], 'mode': 'FAP', 'diagnostics': {'guest': {
                'state': 'available', 'updated_at': time.time(), 'enabled': False,
                'bands': {'band2_4': False, 'band5_1': False}}}}, True)
            bridge = Bridge(options, data, save, controller, stop, '0.8.0-dev.1')
            bridge.start()
            try:
                deadline = time.monotonic()+7
                count = len(ENTITIES) + int(guest_mode)
                while time.monotonic() < deadline and len([t for t, p in messages if t.endswith('/config') and p]) < count:
                    time.sleep(0.05)
                self.assertEqual(errors, [])
                self.assertEqual(len([t for t, p in messages if t.endswith('/config') and p]), count)
                if guest_mode:
                    switch = next(json.loads(p) for t, p in messages if '/switch/' in t and p)
                    self.assertFalse(switch['optimistic'])
                    self.assertEqual(json.loads(switch['payload_on'])['session'], bridge.session)
                self.assertIn((bridge.base+'/command', 40), subscriptions)
                def command(session, retain=False):
                    topic = (bridge.base+'/command').encode()
                    body = struct.pack('>H', len(topic)) + topic + b'\x00' + json.dumps({
                        'action': 'guest_on' if guest_mode else 'optimize', 'session': session}).encode()
                    send(bytes([0x31 if retain else 0x30]) + variable(len(body)) + body)
                command(bridge.session, retain=True)
                command('previous-connection')
                time.sleep(0.1)
                self.assertIsNone(controller.pending)
                command(bridge.session)
                command(bridge.session)
                deadline = time.monotonic()+2
                while controller.pending is None and time.monotonic() < deadline: time.sleep(0.02)
                self.assertIsNotNone(controller.pending)
                stored = json.loads((data/('guest_wifi_state.json' if guest_mode else 'automation_state.json')).read_text())
                self.assertTrue(stored['inflight'])
                self.assertEqual(stored['request_id' if guest_mode else 'last_id'], controller.pending['request_id'])
                self.assertFalse((data/'tmp_optimize_journal.json').exists())  # No worker/Deco contacted.
            finally:
                stop.set()
                bridge.thread.join(timeout=5)
                for peer in peers: peer.close()
                server.close()
                thread.join(timeout=2)
        self.assertEqual(errors, [])


if __name__ == '__main__': unittest.main()
