"""Home Assistant adapter for the physically validated X50 TPAP client."""
import hashlib
import ipaddress
import json
import math
import logging
import re
import time

import x50_tpap as protocol


def certificate_mode(options):
    mode = options.get('x50_certificate_mode', 'automatic')
    if mode not in ('automatic', 'pinned'):
        raise ValueError()
    return mode


def resolve_certificate(options, data, save):
    """Enroll a vendor-verified certificate once; never silently replace identity."""
    mode = certificate_mode(options)
    if options.get('x50_certificate_sha256', '').strip():
        settings(options)
        return
    if mode == 'pinned':
        raise ValueError('x50_certificate_required')
    settings(options, allow_missing_pin=True)
    path = data / 'x50_certificate_state.json'
    host = str(ipaddress.ip_address(options['host']))
    if path.exists():
        stored = json.loads(path.read_text())
        if stored.get('host') != host or not re.fullmatch('[a-f0-9]{64}', stored.get('sha256', '')):
            raise ValueError('x50_certificate_state_invalid')
        pin = stored['sha256']
    else:
        result = protocol.probe(host, timeout=options.get('tmp_timeout_seconds', 10))
        pin = result.get('certificate_sha256', '')
        if (result.get('state') != 'tls_verified' or result.get('vendor_chain_verified') is not True
                or not re.fullmatch('[a-f0-9]{64}', pin)):
            raise ValueError('x50_certificate_discovery_failed')
        save(path, {'version': 1, 'host': host, 'sha256': pin})
    options['x50_certificate_sha256'] = pin


def settings(options, allow_missing_pin=False):
    certificate_mode(options)
    address = ipaddress.ip_address(options['host'])
    pin = options.get('x50_certificate_sha256', '').strip().lower()
    email = options.get('tmp_email', '').strip()
    password = options.get('tmp_password', '')
    if (address.is_unspecified or address.is_multicast
            or not (re.fullmatch('[a-f0-9]{64}', pin) or (allow_missing_pin and not pin))
            or not re.fullmatch(r'[^@\s]+@[^@\s]+', email) or len(email.encode()) > 254
            or not isinstance(password, str) or not 1 <= len(password.encode()) <= 256
            or '\0' in email or '\0' in password):
        raise ValueError()
    result = dict(host=str(address), expected_sha256=pin, owner_email=email, password=password)
    for option, key, default, low, high in (
        ('tmp_timeout_seconds', 'timeout', 10, 5, 30),
        ('tmp_scan_timeout_seconds', 'scan_timeout', 120, 30, 180),
        ('tmp_apply_timeout_seconds', 'apply_timeout', 60, 10, 180),
        ('tmp_verify_timeout_seconds', 'verify_timeout', 120, 30, 600),
    ):
        value = options.get(option, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError()
        result[key] = min(value, 300) if key == 'verify_timeout' else value
    return result


def target_hash(options):
    value = settings(options)
    return hashlib.sha256((value['host'] + '|' + value['expected_sha256']).encode()).hexdigest()


def load_journal(data):
    path = data / 'x50_optimize_journal.json'
    with protocol.action_store(path) as (actions, _):
        return path, {'version': 1, 'actions': {
            key: {'target_hash': entry['target'], 'report': entry['report']}
            for key, entry in actions.items()}}


def prepare(options, data, now, verify=False):
    try:
        configured = settings(options)
        request_id = options.get('tmp_optimize_request_id', '').strip()
        if not re.fullmatch('[A-Za-z0-9_-]{1,48}', request_id):
            return None, {'state': 'optimize_id_required'}
        with protocol.action_store(data / 'x50_optimize_journal.json') as (actions, _):
            error = protocol.action_error(actions, target_hash(options), request_id, now, verify)
        return configured, error
    except BlockingIOError:
        return None, {'state': 'action_in_progress'}
    except Exception:
        return None, {'state': 'invalid_x50_configuration_or_journal'}


class LoginGuard:
    """Reserve every login durably; release only after mutual password proof."""
    def __init__(self, data, save, should_stop=None, clock=time.time):
        self.path, self.save, self.should_stop, self.clock = data / 'x50_auth_state.json', save, should_stop, clock

    def before(self):
        if self.should_stop and self.should_stop():
            raise protocol.ActionCancelled()
        try:
            due = json.loads(self.path.read_text())['next_login'] if self.path.exists() else 0
            if type(due) not in (float, int) or not math.isfinite(due) or due < 0:
                raise ValueError()
            if self.clock() < due:
                raise protocol.ProtocolError('authentication_cooldown')
            self.save(self.path, {'next_login': self.clock() + 900})
        except protocol.ProtocolError:
            raise
        except Exception:
            raise protocol.ActionStorageError() from None

    def verified(self):
        try:
            self.save(self.path, {'next_login': 0})
        except Exception:
            raise protocol.ActionStorageError() from None


def run_probe(options, data, save, *, should_stop=None, wait=None, guest_job=None, guest_control=None, **unused):
    if guest_job is not None:
        import guest_wifi
        if (not guest_wifi.enabled(options) or not options.get('mqtt_enabled')
                or not options.get('mqtt_commands_enabled') or not options.get('tmp_enabled')
                or guest_control is None):
            return {'state': 'guest_control_disabled'}
        configured = settings(options)
        guard = LoginGuard(data, save, should_stop)
        result = protocol.probe(configured['host'], timeout=configured['timeout'],
                                expected_sha256=configured['expected_sha256'],
                                owner_email=configured['owner_email'], password=configured['password'],
                                read_info=True, _before_connect=guard.before, _login_verified=guard.verified,
                                _guest_callback=lambda reader: guest_wifi.execute(reader, options, data,
                                    guest_control, guest_job, should_stop=should_stop or (lambda: False), wait=wait))
        return result.get('guest_action', {'state': 'guest_connection_failed',
                                         'connection_state': result['state'], 'write_attempted': False,
                                         'connection_diagnostic': connection_diagnostic(result)})
    action = options.get('tmp_action')
    if action not in ('optimize', 'verify') or not options.get('tmp_enabled', False):
        return {'state': 'commands_disabled'}
    configured, error = prepare(options, data, time.time(), verify=action == 'verify')
    if error:
        return error
    guard = LoginGuard(data, save, should_stop)
    def progress(report):
        save(data / 'x50_optimize_report.json', report)
    from m9_actions import action_lock
    try:
        with action_lock(data):
            return protocol.run_action(**configured, request_id=options['tmp_optimize_request_id'].strip(),
                                       journal=data / 'x50_optimize_journal.json', verify_only=action == 'verify',
                                       _before_connect=guard.before, _login_verified=guard.verified,
                                       _progress=progress, _wait=wait, _should_stop=should_stop)
    except BlockingIOError:
        return {'state': 'action_in_progress'}


class AuthenticatedReadUnavailable(ValueError):
    """Password proof succeeded; polling failure must not imply bad credentials."""


def connection_diagnostic(result):
    """Only fixed public states and typed booleans; never raw protocol messages."""
    states = {'authentication_rejected', 'server_proof_mismatch', 'readonly_incomplete',
              'protocol_error', 'certificate_untrusted', 'certificate_pin_mismatch',
              'unexpected_certificate_model', 'timeout', 'connection_refused',
              'tls_error', 'connection_error', 'readonly_verified', 'pake_parameters_received'}
    report = {'state': result.get('state') if result.get('state') in states else 'unrecognized_result'}
    for key in ('vendor_chain_verified', 'certificate_pin_matched', 'password_proof_attempted',
                'password_proof_sent', 'server_proof_verified', 'account_credentials_verified',
                'session_token_received'):
        if type(result.get(key)) is bool:
            report[key] = result[key]
    stages = {'tmp_allocate', 'read_mode', 'read_channels', 'read_devices', 'complete'}
    if result.get('stage') in stages:
        report['stage'] = result['stage']
    errors = {'tmp_message_checksum_mismatch', 'tmp_request_rejected', 'tmp_fragment_invalid', 'tmp_result_too_large',
              'tmp_empty_fragment', 'tmp_frame_limit', 'unexpected_tmp_header',
              'invalid_tmp_length', 'tmp_checksum_mismatch', 'tmp_business_invalid',
              'tmp_response_mismatch', 'response_truncated', 'invalid_tmp_session',
              'channel_tag_mismatch', 'channel_checksum_mismatch', 'invalid_channel_length',
              'repeated_channel_sequence', 'channel_receive_limit', 'unexpected_channel_header'}
    if result.get('protocol_error') in errors:
        report['protocol_error'] = result['protocol_error']
    if type(result.get('tmp_error_code')) is int:
        report['tmp_error_code'] = result['tmp_error_code']
    framing = result.get('tmp_business_diagnostic')
    if isinstance(framing, dict):
        clean = {key: framing[key] for key in
                 ('version', 'subversion', 'opcode', 'kind', 'error', 'chunk_bytes', 'total_bytes', 'offset')
                 if type(framing.get(key)) is int and 0 <= framing[key] <= 0xffffffff}
        clean.update({key: framing[key] for key in
                      ('version_matches', 'chunk_crc_matches', 'assembled_crc_matches')
                      if type(framing.get(key)) is bool})
        report['tmp_business_diagnostic'] = clean
    reads = result.get('reads', {})
    report['reads'] = {key: {'state': value.get('state') if value.get('state') in
                            ('available', 'unsupported', 'error', 'unavailable') else 'unrecognized',
                            **({'api_code': value['api_code']} if type(value.get('api_code')) is int else {})}
                       for key, value in reads.items() if key in ('mode', 'devices', 'channels')
                       and isinstance(value, dict)} if isinstance(reads, dict) else {}
    return report


class Client:
    """Polling reads mode, inventory and backhaul only. No ACS request here."""
    def __init__(self, options, data, save, stop):
        self.options, self.configured = options, settings(options)
        self.data, self.save = data, save
        self.guard = LoginGuard(data, save, stop.is_set)
        self.cached = None

    def read(self):
        c = self.configured
        import guest_wifi
        result = protocol.probe(c['host'], timeout=c['timeout'], expected_sha256=c['expected_sha256'],
                                owner_email=c['owner_email'], password=c['password'], read_info=True,
                                _before_connect=self.guard.before, _login_verified=self.guard.verified,
                                _guest_read=guest_wifi.enabled(self.options),
                                _node_read=self.options.get('monitor_nodes', True),
                                _performance_read=self.options.get('monitor_performance', True))
        if result['state'] != 'readonly_verified':
            diagnostic = connection_diagnostic(result)
            self.save(self.data / 'x50_connection_report.json', diagnostic)
            logging.getLogger(__name__).warning('Diagnóstico de conexão X50: %s', json.dumps(diagnostic))
            if result.get('server_proof_verified') is True:
                raise AuthenticatedReadUnavailable('x50_read_unavailable')
            raise ValueError('x50_read_unavailable')
        return result

    def login(self):
        self.cached = self.read()

    def collect(self, cycle, version):
        result = self.cached if self.cached is not None else self.read()
        self.cached = None
        now = int(time.time())
        reads = result['reads']
        return {'updated_at': now, 'available': True, 'cycle': cycle, 'version': version,
                'backend': 'x50_tpap', 'read_only': True,
                'profile_supported': protocol.supported_target(result),
                **({'mesh_nodes': result['mesh_nodes'], 'mesh_at': now} if 'mesh_nodes' in result else {}),
                'nodes': reads['devices']['data']['nodes'], 'mode': reads['mode']['data']['mode'],
                'diagnostics': {**({'performance': dict(result['performance'], updated_at=now)} if 'performance' in result else {}),
                                'channels': dict(reads['channels'], updated_at=now),
                                **({'guest': dict(result['guest'], updated_at=now)} if 'guest' in result else {})},
                'unsupported': ['acs_periodic', 'wifi_support'] + ([] if result.get('performance', {}).get('state') == 'available' else ['performance'])}
