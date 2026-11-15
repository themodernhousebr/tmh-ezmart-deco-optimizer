"""Home Assistant adapter for the physically validated X50 TPAP client."""
import hashlib
import ipaddress
import json
import math
import re
import time

import x50_tpap as protocol


def settings(options):
    address = ipaddress.ip_address(options['host'])
    pin = options.get('x50_certificate_sha256', '').strip().lower()
    email = options.get('tmp_email', '').strip()
    password = options.get('tmp_password', '')
    if (address.is_unspecified or address.is_multicast or not re.fullmatch('[a-f0-9]{64}', pin)
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


def run_probe(options, data, save, *, should_stop=None, wait=None, **unused):
    action = options.get('tmp_action')
    if action not in ('optimize', 'verify') or not options.get('tmp_enabled', False):
        return {'state': 'commands_disabled'}
    configured, error = prepare(options, data, time.time(), verify=action == 'verify')
    if error:
        return error
    guard = LoginGuard(data, save, should_stop)
    def progress(report):
        save(data / 'x50_optimize_report.json', report)
    return protocol.run_action(**configured, request_id=options['tmp_optimize_request_id'].strip(),
                               journal=data / 'x50_optimize_journal.json', verify_only=action == 'verify',
                               _before_connect=guard.before, _login_verified=guard.verified,
                               _progress=progress, _wait=wait, _should_stop=should_stop)


class Client:
    """Polling reads mode, inventory and backhaul only. No ACS request here."""
    def __init__(self, options, data, save, stop):
        self.options, self.configured = options, settings(options)
        self.guard = LoginGuard(data, save, stop.is_set)
        self.cached = None

    def read(self):
        c = self.configured
        result = protocol.probe(c['host'], timeout=c['timeout'], expected_sha256=c['expected_sha256'],
                                owner_email=c['owner_email'], password=c['password'], read_info=True,
                                _before_connect=self.guard.before, _login_verified=self.guard.verified)
        if result['state'] != 'readonly_verified':
            # Fixed public error, never raw protocol/credentials.
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
                'nodes': reads['devices']['data']['nodes'], 'mode': reads['mode']['data']['mode'],
                'diagnostics': {'channels': dict(reads['channels'], updated_at=now)},
                'unsupported': ['acs_periodic', 'performance', 'wifi_support']}
