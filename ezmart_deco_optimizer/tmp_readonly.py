"""Fixed TMP operations with separate gates for manual scans and M9 application."""
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
# M9 V2 firmware maps this to acs_optimize/read, which can START a scan.
M9_ACS_SCAN = 0x424F
M9_ACS_APPLY = 0x4250
# Legacy 0x4266, arbitrary writes and shell commands remain unavailable.
MAX_RESULT = 262144


class ProtocolError(Exception):
    pass


def packet(opcode, body, transaction, sequence, offset=0, pull=False, allow_scan=False, allow_acs_scan=False, allow_acs_apply=False, allow_guest=False, allow_profile_read=False):
    if (opcode not in (ALLOC, QUALITY) and not (allow_scan and opcode == SCAN)
            and not (allow_acs_scan and opcode == M9_ACS_SCAN)
            and not (allow_acs_apply and opcode == M9_ACS_APPLY)
            and not (allow_guest and opcode in (0x4008, 0x4009, 0x400F, 0x40A0))
            and not (allow_profile_read and opcode in (0x400F, 0x40A0))):
        raise ProtocolError()
    business = struct.pack('>BBHBBHIII', 1, 2, opcode, 4 if pull else 2,
                           0, transaction, zlib.crc32(body) if body else 0, len(body), offset) + body
    header = struct.pack('>BBBBHHI', 1, 1, 5, 0, len(business), 0, sequence)
    checksum = zlib.crc32(header + bytes.fromhex('5a6b7c8d') + business)
    return header + struct.pack('>I', checksum) + business


class Reader:
    def __init__(self, channel, allow_scan=False):
        self.allow_scan = allow_scan
        self.allow_acs_scan = False
        self.allow_acs_apply = False
        self.allow_guest = False
        self.allow_profile_read = False
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
            self.channel.settimeout(remaining)
            part = self.channel.recv(size - len(result))
            if not part:
                raise ProtocolError()
            result.extend(part)
        return bytes(result)

    def send(self, opcode, body, pull=False, offset=0):
        self.sequence += 1
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        self.channel.settimeout(remaining)
        self.channel.sendall(packet(opcode, body, self.transaction, self.sequence, offset, pull,
                                    self.allow_scan, self.allow_acs_scan, self.allow_acs_apply, self.allow_guest, self.allow_profile_read))

    def request(self, opcode, timeout_seconds=20):
        if opcode not in (ALLOC, QUALITY, SCAN, M9_ACS_SCAN, M9_ACS_APPLY):
            raise ProtocolError()
        params = {} if opcode in (M9_ACS_SCAN, M9_ACS_APPLY) else {'scan_type': ['network_quality']}
        return self._exchange(opcode, params, timeout_seconds)

    def profile_request(self, opcode):
        if opcode not in (0x400F, 0x40A0):
            raise ProtocolError()
        self.allow_profile_read = True
        self.profile_diagnostic = {}
        try:
            return self._exchange(opcode, {}, 30)
        finally:
            self.allow_profile_read = False

    def guest_request(self, opcode, params=None):
        from guest_wifi import READ_OPS, WRITE_WLAN, validate_params
        if not self.allow_guest or opcode not in (*READ_OPS, WRITE_WLAN):
            raise ProtocolError()
        if opcode == WRITE_WLAN:
            validate_params(params)
        elif params is not None:
            raise ProtocolError()
        return self._exchange(opcode, params or {}, 20)

    def _exchange(self, opcode, params, timeout_seconds):
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 180:
            raise ProtocolError()
        self.deadline = time.monotonic() + timeout_seconds
        self.transaction += 1
        request = {'config_version': int(time.time()*1000), 'params': params}
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
                if self.allow_profile_read:
                    self.profile_diagnostic = {'version':version, 'subversion':subversion,
                        'opcode':returned_op, 'kind':kind, 'error':error,
                        'chunk_bytes':len(chunk), 'total_bytes':total, 'offset':offset,
                        'chunk_crc_matches':zlib.crc32(chunk) == crc,
                        'assembled_crc_matches':zlib.crc32(bytes(result) + chunk) == crc}
                raise ProtocolError()
            if tx != self.transaction:
                continue
            if returned_op != opcode or kind != 5 or error or total > MAX_RESULT:
                if self.allow_profile_read:
                    self.profile_diagnostic = {'opcode':returned_op, 'kind':kind, 'error':error,
                        'chunk_bytes':len(chunk), 'total_bytes':total, 'offset':offset}
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


def project_m9_acs(document):
    """Whitelist the actual M9 controller response, not arbitrary result fields."""
    if not isinstance(document, dict) or type(document.get('error_code')) is not int:
        return {'state': 'scan_response_unrecognized'}
    code = document['error_code']
    if code != 0:
        return {'state': 'scan_rejected', 'api_code': code}
    result = document.get('result')
    if not isinstance(result, dict):
        return {'state': 'scan_response_unrecognized', 'api_code': 0}
    needed = result.get('need_optimize')
    items = result.get('optimize_item')
    channel = items.get('channel') if isinstance(items, dict) else None
    if type(needed) is not bool or type(channel) is not bool or needed != channel:
        return {'state': 'scan_response_unrecognized', 'api_code': 0}
    out = {'state': 'scan_response_received', 'api_code': 0, 'need_optimize': needed,
           'optimize_item': {'channel': channel}, 'fresh_scan_confirmed': False,
           'result_may_be_cached': True}
    estimated = result.get('optimize_time')
    if type(estimated) is int and 0 <= estimated <= 600:
        # This is the device's APPLY estimate, not the duration of the scan.
        out['estimated_apply_seconds'] = estimated
    return out


def scan_request(options, data):
    """Validate action and replay protection before any SSH connection."""
    backend = options.get('tmp_scan_backend', 'legacy')
    if backend not in ('legacy', 'm9_acs'):
        return None, {'state': 'invalid_scan_backend'}
    request_id = options.get('tmp_scan_request_id', '')
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', request_id):
        return None, {'state': 'scan_id_required'}
    wait = options.get('tmp_scan_timeout_seconds', 90)
    if type(wait) is not int or not 30 <= wait <= 180:
        return None, {'state': 'invalid_scan_timeout'}
    journal = data / 'tmp_scan_journal.json'
    try:
        history = json.loads(journal.read_text()) if journal.exists() else {}
    except (OSError, ValueError):
        return None, {'state': 'invalid_scan_journal'}
    if not isinstance(history, dict):
        return None, {'state': 'invalid_scan_journal'}
    if request_id in history:
        return None, {'state': 'already_requested', 'report': history[request_id]}
    if len(history) >= 1000:
        return None, {'state': 'scan_history_full'}
    return (request_id, backend, wait, journal, history), None


def run_probe(options, data, save, clock=time.time, channel_snapshot=None, should_stop=lambda: False, wait=None,
              guest_job=None, guest_control=None):
    """One connection per process. A missing pin discovers identity without auth."""
    if options.get('deco_model') == 'm5' and guest_job is None and options.get('tmp_action') not in ('optimize', 'verify'):
        return {'state': 'm5_action_not_supported', 'write_attempted': False}
    action = 'read' if guest_job is not None else options.get('tmp_action', 'read')
    if guest_job is not None:
        from guest_wifi import enabled
        if (options.get('deco_model', 'm9_plus') not in ('m9_plus', 'm5')
                or not enabled(options) or not options.get('mqtt_enabled') or not options.get('mqtt_commands_enabled')
                or not options.get('tmp_enabled') or guest_control is None):
            return {'state': 'guest_control_disabled'}
    if action not in ('read', 'scan', 'optimize', 'verify'):
        return {'state': 'invalid_action'}
    if action in ('optimize', 'verify'):
        from m9_actions import prepare, verify_only
        _, error = prepare(options, data, clock(), verify=action == 'verify')
        if error:
            return error
        if action == 'verify':
            # HTTPS observations only. No SSH login, scan, or application.
            return verify_only(options, data, save, channel_snapshot,
                               should_stop=should_stop, wait=wait, clock=clock)
    if action == 'scan':
        _, error = scan_request(options, data)
        if error:
            return error
    import paramiko
    # Paramiko logs may include authentication metadata: suppress its entire tree.
    for name in list(logging.Logger.manager.loggerDict) + ['paramiko']:
        if name.startswith('paramiko'):
            logging.getLogger(name).disabled = True
    state = data / 'tmp_state.json'
    try:
        due = json.loads(state.read_text()).get('next_login', 0) if state.exists() else 0
        from m9_actions import finite_time
        if not finite_time(due):
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
        mode = options.get('tmp_host_key_mode', 'automatic')
        if mode not in ('automatic', 'pinned'):
            return {'state': 'invalid_host_key_mode'}
        if mode == 'pinned':
            if not pin:
                return {'state': 'host_key_required', 'host_key_sha256': fingerprint}
            if not re.fullmatch('[0-9a-f]{64}', pin) or not hmac.compare_digest(pin, fingerprint):
                return {'state': 'host_key_mismatch'}
        else:
            # Explicit IP-trust policy. The current SSH key secures this
            # session, but does not authenticate the remote device identity.
            logging.getLogger('ezmart').info('%s SSH: confiança no IP reservado; chave obtida nesta conexão.',
                                           'M5' if options.get('deco_model') == 'm5' else 'M9')
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
        if guest_job is not None:
            from guest_wifi import execute
            stage = 'guest_wifi'
            reader.allow_guest = True
            try:
                return execute(reader, options, data, guest_control, guest_job,
                               should_stop=should_stop, wait=wait, clock=clock)
            finally:
                reader.allow_guest = False
        if action == 'optimize':
            from m9_actions import optimize_once
            stage = 'optimize'
            return optimize_once(reader, options, data, save, channel_snapshot,
                                 should_stop=should_stop, wait=wait, clock=clock)
        if action == 'scan' and options.get('tmp_scan_backend') == 'm9_acs':
            stage = 'scan'
            # Legacy 0x4265 is not a reliable preflight for the M9 ACS path.
            return scan_once(reader, None, options, data, save, channel_snapshot=channel_snapshot)
        stage = 'quality_read'
        before = project_quality(json.loads(reader.request(QUALITY)))
        if action == 'scan':
            stage = 'scan'
            return scan_once(reader, before, options, data, save, channel_snapshot=channel_snapshot)
        before['source'] = 'legacy_network_quality'
        before['official_optimization_equivalent'] = False
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


def scan_once(reader, before, options, data, save, monotonic=time.monotonic, sleep=time.sleep, channel_snapshot=None):
    """Durable at-most-once scan dispatch. Never repeats an uncertain action."""
    prepared, error = scan_request(options, data)
    if error:
        return error
    request_id, backend, wait, journal, history = prepared
    if backend == 'legacy' and (not isinstance(before, dict) or before.get('state') != 'idle'):
        return {'state': 'scan_blocked', 'before': before}
    report = {'state': 'dispatch_uncertain', 'backend': backend, 'request_id': request_id,
              'started_at': int(time.time()), 'apply_command_sent': False, 'channels_applied': False}
    if before is not None:
        report['before'] = before
    # Commit the ID before sending: crashes/timeouts must not replay a scan.
    history[request_id] = report
    save(journal, history)
    if backend == 'm9_acs':
        return m9_scan_once(reader, report, wait, journal, history, data, save, channel_snapshot)
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


def m9_scan_once(reader, report, wait, journal, history, data, save, channel_snapshot):
    """Exactly one ACS request. Polling this opcode can launch another scan."""
    def snapshot():
        if channel_snapshot is None:
            return {'state': 'not_requested'}
        try:
            from diagnostics import project
            # Callback returns a raw WLAN response. Project it here before storage.
            value = project('channels', channel_snapshot())
            if not value:
                return {'state': 'unavailable'}
            return {'state': 'available', 'data': value, 'updated_at': int(time.time())}
        except Exception:
            return {'state': 'unavailable'}
    report['channels_before'] = snapshot()
    LOG = logging.getLogger('ezmart')
    LOG.info('TMP M9 ACS: uma solicitação de varredura; aguardando até %s segundos. Sem aplicação de canais.', wait)
    started = time.monotonic()
    try:
        reader.allow_acs_scan = True
        result = reader.request(M9_ACS_SCAN, timeout_seconds=wait)
        report.update(project_m9_acs(json.loads(result)))
    except TimeoutError:
        report['state'] = 'scan_timeout_uncertain'
    except Exception:
        report['state'] = 'scan_outcome_uncertain'
    finally:
        reader.allow_acs_scan = False
    report['response_seconds'] = round(time.monotonic() - started, 3)
    report['finished_at'] = int(time.time())
    # Persist the outcome before optional monitoring, which can itself time out.
    save(journal, history)
    save(data / 'tmp_scan_report.json', report)
    report['channels_after'] = snapshot()
    if report['channels_before']['state'] == report['channels_after']['state'] == 'available':
        report['observed_channel_data_changed'] = report['channels_before']['data'] != report['channels_after']['data']
    save(journal, history)
    save(data / 'tmp_scan_report.json', report)
    return report
