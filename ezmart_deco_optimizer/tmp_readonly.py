"""TMP state reader and explicitly requested scan. No optimize, shell or raw command API."""
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
SCAN = 0x4263
MAX_RESULT = 262144


class ProtocolError(Exception):
    pass


def packet(opcode, body, transaction, sequence, offset=0, pull=False, allow_scan=False):
    if opcode not in (ALLOC, QUALITY) and not (allow_scan and opcode == SCAN):
        raise ProtocolError()
    business = struct.pack('>BBHBBHIII', 1, 2, opcode, 4 if pull else 2,
                           0, transaction, zlib.crc32(body) if body else 0, len(body), offset) + body
    header = struct.pack('>BBBBHHI', 1, 1, 5, 0, len(business), 0, sequence)
    checksum = zlib.crc32(header + bytes.fromhex('5a6b7c8d') + business)
    return header + struct.pack('>I', checksum) + business


class Reader:
    def __init__(self, channel, allow_scan=False):
        self.allow_scan = allow_scan
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
        self.channel.sendall(packet(opcode, body, self.transaction, self.sequence, offset, pull, self.allow_scan))

    def request(self, opcode):
        self.deadline = time.monotonic() + 20
        self.transaction += 1
        # The quality-result read requires a network_quality selector.
        request = {'config_version': int(time.time()*1000),
                   'params': {'scan_type': ['network_quality']}}
        body = b'' if opcode == ALLOC else json.dumps(request, separators=(',', ':')).encode()
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
        before = project_quality(json.loads(reader.request(QUALITY)))
        if options.get('tmp_action', 'read') == 'scan':
            stage = 'scan'
            return scan_once(reader, before, options, data, save)
        return before
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


def scan_once(reader, before, options, data, save, monotonic=time.monotonic, sleep=time.sleep):
    """Durable at-most-once scan dispatch. Never repeats an uncertain action."""
    request_id = options.get('tmp_scan_request_id', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', request_id):
        return {'state': 'scan_id_required'}
    journal = data / 'tmp_scan_journal.json'
    history = json.loads(journal.read_text()) if journal.exists() else {}
    if not isinstance(history, dict):
        return {'state': 'invalid_scan_journal'}
    if request_id in history:
        return {'state': 'already_requested', 'report': history[request_id]}
    if len(history) >= 1000:
        return {'state': 'scan_history_full'}
    wait = options.get('tmp_scan_timeout_seconds', 90)
    if type(wait) is not int or not 30 <= wait <= 180:
        return {'state': 'invalid_scan_timeout'}
    if before.get('state') != 'idle':
        return {'state': 'scan_blocked', 'before': before}
    report = {'state': 'dispatch_uncertain', 'before': before, 'started_at': int(time.time())}
    # Commit the ID before sending: crashes/timeouts must not replay a scan.
    history[request_id] = report
    save(journal, history)
    try:
        reader.allow_scan = True
        ack = json.loads(reader.request(SCAN))
        reader.allow_scan = False
        ack_code = ack.get('error_code') if isinstance(ack, dict) else None
        report['ack_is_object'] = isinstance(ack, dict)
        report['ack_has_error_code'] = isinstance(ack, dict) and 'error_code' in ack
        if type(ack_code) is int:
            report['ack_api_code'] = ack_code
        if not isinstance(ack, dict) or type(ack_code) is not int or ack_code != 0:
            report['state'] = 'scan_rejected' if type(ack_code) is int and ack_code != 0 else 'scan_ack_unrecognized'
        else:
            report['state'] = 'scan_timeout'
            deadline = monotonic() + wait
            observed_busy = False
            while monotonic() < deadline:
                sleep(min(2, max(0, deadline-monotonic())))
                if monotonic() >= deadline:
                    break
                result = project_quality(json.loads(reader.request(QUALITY)))
                observed_busy |= result.get('state') in ('scanning', 'checking')
                report['after'] = result
                if result.get('state') == 'idle':
                    report['state'] = 'scan_finished' if observed_busy else 'idle_after_ack'
                    break
                if result.get('state') in ('api_error', 'unknown', 'unrecognized_response'):
                    report['state'] = 'scan_result_error'
                    break
            report['scan_acknowledged'] = True
            report['observed_scanning'] = observed_busy
    except Exception:
        report['state'] = 'scan_outcome_uncertain'
    finally:
        reader.allow_scan = False
    report['finished_at'] = int(time.time())
    report['channels_applied'] = False
    history[request_id] = report
    save(journal, history)
    save(data / 'tmp_scan_report.json', report)
    return report
