"""Experimental TMP state reader. No scan, optimize, shell or raw command API."""
import hashlib
import hmac
import json
import logging
import re
import socket
import struct
import time
import zlib

ALLOC = 1
QUALITY = 0x4265
MAX_RESULT = 262144


class ProtocolError(Exception):
    pass


def packet(opcode, body, transaction, sequence, offset=0, pull=False):
    if opcode not in (ALLOC, QUALITY):
        raise ProtocolError()
    business = struct.pack('>BBHBBHIII', 1, 2, opcode, 4 if pull else 2,
                           0, transaction, zlib.crc32(body) if body else 0, len(body), offset) + body
    header = struct.pack('>BBBBHHI', 1, 1, 5, 0, len(business), 0, sequence)
    checksum = zlib.crc32(header + bytes.fromhex('5a6b7c8d') + business)
    return header + struct.pack('>I', checksum) + business


class Reader:
    def __init__(self, channel):
        self.channel = channel
        self.sequence = 0
        self.transaction = -1
        self.deadline = 0

    def exact(self, size):
        result = bytearray()
        while len(result) < size:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            self.channel.settimeout(min(remaining, 10))
            part = self.channel.recv(size - len(result))
            if not part:
                raise ProtocolError()
            result.extend(part)
        return bytes(result)

    def send(self, opcode, body, pull=False, offset=0):
        self.sequence += 1
        self.channel.sendall(packet(opcode, body, self.transaction, self.sequence, offset, pull))

    def request(self, opcode):
        self.deadline = time.monotonic() + 20
        self.transaction += 1
        body = b'' if opcode == ALLOC else json.dumps({'config_version': int(time.time()*1000)}, separators=(',', ':')).encode()
        self.send(opcode, body)
        result = bytearray()
        expected_total = None
        while True:
            prefix = self.exact(4)
            if prefix == b'\x01\x01\x04\x00':
                self.channel.sendall(prefix)
                continue
            if prefix == b'\x01\x01\x02\x00':
                continue
            if prefix != b'\x01\x01\x05\x00':
                raise ProtocolError()
            header = prefix + self.exact(12)
            size = struct.unpack_from('>H', header, 4)[0]
            payload = self.exact(size)
            checksum = zlib.crc32(header[:12] + bytes.fromhex('5a6b7c8d') + payload)
            if checksum != struct.unpack_from('>I', header, 12)[0] or len(payload) < 20:
                raise ProtocolError()
            version, subversion, returned_op, kind, error, tx, crc, total, offset = struct.unpack_from('>BBHBBHIII', payload)
            chunk = payload[20:]
            if version != 1 or subversion != 2 or (zlib.crc32(chunk) if chunk else 0) != crc:
                raise ProtocolError()
            if tx != self.transaction:
                continue
            if returned_op != opcode or kind != 5 or error or total > MAX_RESULT:
                raise ProtocolError()
            if offset != len(result) or (expected_total is not None and total != expected_total):
                raise ProtocolError()
            expected_total = total
            result.extend(chunk)
            if len(result) > MAX_RESULT or (total and len(result) > total):
                raise ProtocolError()
            if total == 0 or len(result) == total:
                return bytes(result)
            if not chunk:
                raise ProtocolError()
            self.send(opcode, b'', pull=True, offset=len(result))


def project_quality(document):
    if not isinstance(document, dict):
        raise ProtocolError()
    if document.get('error_code', 0) != 0:
        code = document.get('error_code')
        out = {'state': 'api_error'}
        if type(code) is int:
            out['api_code'] = code
        return out
    result = document.get('result', document)
    quality = result.get('network_quality') if isinstance(result, dict) else None
    if not isinstance(quality, dict):
        return {'state': 'unrecognized_response'}
    states = {'idle', 'scanning', 'optimizing', 'checking', 'optimizable', 'optimal'}
    out = {'state': quality.get('state') if quality.get('state') in states else 'unknown'}
    details = quality.get('detail', [])
    out['details'] = [item['state'] for item in details[:32]
                      if isinstance(item, dict) and item.get('state') in states] if isinstance(details, list) else []
    out['fresh_scan_requested'] = False
    return out


def run_probe(options, data, save, clock=time.time):
    """One connection per process. A missing pin discovers identity without auth."""
    import paramiko
    # Paramiko logs may include authentication metadata: suppress its entire tree.
    for name in list(logging.Logger.manager.loggerDict) + ['paramiko']:
        if name.startswith('paramiko'):
            logging.getLogger(name).disabled = True
    state = data / 'tmp_state.json'
    try:
        due = json.loads(state.read_text()).get('next_login', 0) if state.exists() else 0
        if type(due) not in (int, float) or due < 0:
            return {'state': 'invalid_state'}
        if due > clock():
            return {'state': 'cooldown', 'remaining_seconds': int(due-clock())+1}
    except Exception:
        return {'state': 'invalid_state'}
    transport = None
    sock = None
    channel = None
    stage = 'handshake'
    timeout = options.get('tmp_timeout_seconds', 10)
    if type(timeout) is not int or not 5 <= timeout <= 30:
        return {'state': 'invalid_timeout'}
    try:
        sock = socket.create_connection((options['host'], 20001), timeout=timeout)
        transport = paramiko.Transport(sock)
        transport.banner_timeout = timeout
        transport.auth_timeout = timeout
        transport.start_client(timeout=timeout)
        if not transport.is_active():
            return {'state': 'handshake_failed'}
        fingerprint = hashlib.sha256(transport.get_remote_server_key().asbytes()).hexdigest()
        pin = options.get('tmp_host_key_sha256', '').strip().lower()
        if not pin:
            return {'state': 'host_key_required', 'host_key_sha256': fingerprint}
        if not re.fullmatch('[0-9a-f]{64}', pin) or not hmac.compare_digest(pin, fingerprint):
            return {'state': 'host_key_mismatch'}
        email = options.get('tmp_email', '').strip().lower()
        password = options.get('tmp_password', '')
        if not email or not password:
            return {'state': 'credentials_required'}
        stage = 'authentication'
        save(state, {'next_login': clock()+900})
        user = hashlib.sha1(email.encode()).hexdigest()
        transport.auth_password(user, password, fallback=False)
        if not transport.is_authenticated():
            return {'state': 'authentication_failed'}
        save(state, {'next_login': 0})
        stage = 'channel'
        channel = transport.open_channel('direct-tcpip', ('127.0.0.1', 20002), ('127.0.0.1', 0), timeout=timeout)
        channel.settimeout(timeout)
        stage = 'allocate'
        reader = Reader(channel)
        token = reader.request(ALLOC)
        if not re.fullmatch(b'[0-9a-fA-F]{32}', token):
            raise ProtocolError()
        del token
        stage = 'quality_read'
        return project_quality(json.loads(reader.request(QUALITY)))
    except Exception:
        # Keep interrupted/failed login guard. Never log exception strings or tokens.
        if stage == 'authentication':
            save(state, {'next_login': clock()+900})
        return {'state': 'failed', 'stage': stage}
    finally:
        for resource in (channel, transport, sock):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass
