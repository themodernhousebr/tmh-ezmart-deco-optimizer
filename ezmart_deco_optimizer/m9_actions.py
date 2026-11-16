"""Manual M9 ACS application. Never replay writes; verification is HTTPS-only."""
from contextlib import contextmanager
import fcntl
import hashlib
import ipaddress
import json
import logging
import math
import os
import re
import time

from diagnostics import project

SCAN = 0x424F
APPLY = 0x4250
APPLY_COOLDOWN = 900
LOG = logging.getLogger('ezmart')


def finite_time(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def target_hash(options):
    host = str(ipaddress.ip_address(options['host']))
    pin = options.get('tmp_host_key_sha256', '').strip().lower()
    mode = options.get('tmp_host_key_mode', 'automatic')
    if mode not in ('automatic', 'pinned'):
        raise ValueError()
    if mode == 'automatic' and not pin:
        pin = 'reserved-ip'
    elif not re.fullmatch(r'[0-9a-f]{64}', pin):
        raise ValueError()
    # Retain legacy target identity when a configured pin exists: changing
    # trust mode must not discard cooldowns or replay persisted requests.
    return hashlib.sha256((host + '|' + pin).encode()).hexdigest()


def settings(options):
    if options.get('tmp_scan_backend') != 'm9_acs':
        raise ValueError()
    result = {}
    for key, default, lower, upper in (
        ('tmp_scan_timeout_seconds', 90, 30, 180),
        ('tmp_apply_timeout_seconds', 60, 10, 180),
        ('tmp_verify_delay_seconds', 30, 5, 180),
        ('tmp_verify_timeout_seconds', 120, 30, 600),
        ('tmp_verify_interval_seconds', 10, 5, 60),
    ):
        value = options.get(key, default)
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError()
        result[key] = value
    # At least three samples must fit in the observation window.
    if result['tmp_verify_timeout_seconds'] < 2 * result['tmp_verify_interval_seconds']:
        raise ValueError()
    return result


def load_journal(data):
    path = data / 'tmp_optimize_journal.json'
    if not path.exists():
        return path, {'version': 1, 'actions': {}}
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError()
    journal = json.loads(path.read_text())
    if not isinstance(journal, dict) or journal.get('version') != 1 or not isinstance(journal.get('actions'), dict):
        raise ValueError()
    for key, entry in journal['actions'].items():
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', key):
            raise ValueError()
        if not isinstance(entry, dict) or not isinstance(entry.get('target_hash'), str):
            raise ValueError()
        if not re.fullmatch('[0-9a-f]{64}', entry['target_hash']):
            raise ValueError()
        report = entry.get('report')
        if not isinstance(report, dict) or report.get('request_id') != key:
            raise ValueError()
        if type(report.get('apply_command_attempted')) is not bool:
            raise ValueError()
        if report['apply_command_attempted'] and not finite_time(report.get('apply_attempted_at')):
            raise ValueError()
        if 'verification' in report and not isinstance(report['verification'], dict):
            raise ValueError()
        if 'apply_ack' in report and not isinstance(report['apply_ack'], dict):
            raise ValueError()
        if report['apply_command_attempted']:
            # A durable write reservation always contains a valid baseline.
            baseline = report.get('channels_before_apply')
            if not isinstance(baseline, dict) or not channel_map(baseline):
                raise ValueError()
    return path, journal


def prepare(options, data, now, verify=False):
    """Read-only preflight, repeated under the process lock before any action."""
    try:
        config = settings(options)
        target = target_hash(options)
        request_id = options.get('tmp_optimize_request_id', '')
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', request_id):
            return None, {'state': 'optimize_id_required'}
    except (ValueError, TypeError, KeyError, AttributeError):
        return None, {'state': 'invalid_optimize_configuration'}
    try:
        path, journal = load_journal(data)
    except (OSError, ValueError, TypeError):
        return None, {'state': 'invalid_optimize_journal'}
    actions = journal['actions']
    entry = actions.get(request_id)
    if entry:
        if entry['target_hash'] != target:
            return None, {'state': 'target_mismatch'}
        if not verify:
            return None, {'state': 'already_requested', 'report': entry['report']}
        if not entry['report']['apply_command_attempted']:
            return None, {'state': 'nothing_to_verify', 'report': entry['report']}
    elif verify:
        return None, {'state': 'optimize_report_not_found'}
    if not verify:
        if len(actions) >= 1000:
            return None, {'state': 'optimize_history_full'}
        for previous in actions.values():
            report = previous['report']
            if previous['target_hash'] != target or not report['apply_command_attempted']:
                continue
            if report.get('verification', {}).get('state') != 'stable':
                return None, {'state': 'previous_apply_unverified', 'previous_request_id': report['request_id']}
            remaining = report['apply_attempted_at'] + APPLY_COOLDOWN - now
            if remaining > 0:
                return None, {'state': 'apply_cooldown', 'remaining_seconds': math.ceil(remaining)}
    return (config, target, request_id, path, journal), None


@contextmanager
def action_lock(data):
    fd = os.open(data / 'tmp_optimize.lock', os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def numeric_channel(value):
    if type(value) is str and re.fullmatch(r'[0-9]{1,3}', value):
        value = int(value)
    return value if type(value) is int and 1 <= value <= 233 else None


def channel_map(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get('state') != 'available' or not isinstance(snapshot.get('data'), dict):
        return {}
    return {band: numeric_channel(config.get('channel')) for band, config in snapshot['data'].items()
            if isinstance(config, dict) and numeric_channel(config.get('channel')) is not None}


def snapshot(callback, clock):
    try:
        if callback is None:
            return {'state': 'unavailable'}
        observed = {'state': 'available', 'data': project('channels', callback()), 'updated_at': int(clock())}
        return observed if channel_map(observed) else {'state': 'unavailable'}
    except Exception:
        return {'state': 'unavailable'}


def response_shape(document):
    """Fixed shape metadata, never returned keys, messages, tokens or values."""
    types = {dict: 'object', list: 'array', bool: 'boolean', int: 'integer', float: 'number', str: 'string', type(None): 'null'}
    out = {'root_type': types.get(type(document), 'unknown')}
    if isinstance(document, dict):
        out['has_error_code'] = 'error_code' in document
        out['error_code_type'] = types.get(type(document.get('error_code')), 'unknown')
        out['result_type'] = types.get(type(document.get('result')), 'unknown')
    return out


def project_ack(document):
    out = {'state': 'unrecognized', 'shape': response_shape(document)}
    if isinstance(document, dict) and type(document.get('error_code')) is int:
        out['api_code'] = document['error_code']
        out['state'] = 'accepted' if document['error_code'] == 0 else 'rejected'
    return out


def persist(path, journal, data, save, report):
    # Caller supplies the same report object referenced by the journal entry.
    save(path, journal)
    save(data / 'tmp_optimize_report.json', report)


def default_wait(seconds):
    time.sleep(seconds)
    return False


def observe(report, config, callback, clock, monotonic, wait, should_stop, checkpoint):
    verification = {'state': 'waiting', 'attempts': 0, 'failures': 0,
                    'stable_samples': 0, 'started_at': int(clock())}
    report['verification'] = verification
    checkpoint()
    delay = config['tmp_verify_delay_seconds']
    LOG.info('Otimização: aguardando %s segundos antes de verificar os canais; sem reenviar aplicação.', delay)
    if should_stop() or wait(delay) or should_stop():
        verification['state'] = 'interrupted'
    else:
        deadline = monotonic() + config['tmp_verify_timeout_seconds']
        previous = None
        baseline = channel_map(report['channels_before_apply'])
        while monotonic() <= deadline:
            if should_stop():
                verification['state'] = 'interrupted'
                break
            current = snapshot(callback, clock)
            verification['attempts'] += 1
            current_map = channel_map(current)
            if current_map and baseline.keys() <= current_map.keys():
                verification['last_available'] = current
                verification['stable_samples'] = verification['stable_samples'] + 1 if current_map == previous else 1
                previous = current_map
                LOG.info('Otimização: observação %s; %s de 3 leituras consecutivas com os mesmos canais.',
                         verification['attempts'], verification['stable_samples'])
                if verification['stable_samples'] >= 3:
                    verification['state'] = 'stable'
                    break
            else:
                verification['failures'] += 1
                verification['stable_samples'] = 0
                previous = None
                LOG.info('Otimização: observação %s sem todos os canais; aguardando recuperação, sem reaplicar.',
                         verification['attempts'])
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            verification['state'] = 'observing'
            checkpoint()
            if wait(min(config['tmp_verify_interval_seconds'], remaining)):
                verification['state'] = 'interrupted'
                break
        if verification['state'] not in ('stable', 'interrupted'):
            verification['state'] = 'timeout'
    verification['finished_at'] = int(clock())
    final = verification.get('last_available')
    if final:
        before, after = channel_map(report['channels_before_apply']), channel_map(final)
        changes = [{'band': band, 'before': value, 'after': after[band]}
                   for band, value in before.items() if band in after and value != after[band]]
        verification['channel_changes'] = changes
        verification['observed_channels_changed'] = bool(changes)
    acknowledged = report.get('apply_ack', {}).get('state') == 'accepted'
    if verification['state'] == 'stable':
        suffix = 'with_channel_change' if verification.get('observed_channels_changed') else 'without_observed_change'
        report['conclusion'] = ('acknowledged_' if acknowledged else 'unconfirmed_') + suffix
    else:
        report['conclusion'] = 'verification_incomplete'
    # A rejected response remains a rejection even if some channels changed.
    if report.get('apply_ack', {}).get('state') == 'rejected':
        report['conclusion'] = 'apply_rejected'
    checkpoint()
    LOG.info('Otimização: verificação %s; conclusão %s.', verification['state'], report['conclusion'])


def optimize_once(reader, options, data, save, channel_snapshot, *, clock=time.time,
                  monotonic=time.monotonic, wait=None, should_stop=lambda: False):
    from tmp_readonly import project_m9_acs
    wait = wait or default_wait
    try:
        with action_lock(data):
            prepared, error = prepare(options, data, clock())
            if error:
                return error
            config, target, request_id, path, journal = prepared
            report = {'state': 'preflight_reserved', 'request_id': request_id, 'backend': 'm9_acs',
                      'started_at': int(clock()), 'apply_command_attempted': False}
            journal['actions'][request_id] = {'target_hash': target, 'report': report}
            checkpoint = lambda: persist(path, journal, data, save, report)
            checkpoint()  # Consumes the ID before the active preflight scan.
            if should_stop():
                report['state'] = 'cancelled_before_scan'
                checkpoint()
                return report
            LOG.info('Otimização manual: análise M9 antes de decidir se há canais a aplicar.')
            started = monotonic()
            try:
                reader.allow_acs_scan = True
                document = json.loads(reader.request(SCAN, timeout_seconds=config['tmp_scan_timeout_seconds']))
                preflight = project_m9_acs(document)
                preflight['shape'] = response_shape(document)
            except Exception:
                preflight = {'state': 'scan_outcome_uncertain'}
            finally:
                reader.allow_acs_scan = False
            report['preflight'] = preflight
            report['preflight_seconds'] = round(monotonic() - started, 3)
            if preflight['state'] != 'scan_response_received':
                report['state'] = 'preflight_failed'
            elif preflight['need_optimize'] is False:
                report['state'] = 'optimization_not_needed'
            elif should_stop():
                report['state'] = 'cancelled_before_apply'
            else:
                report['channels_before_apply'] = snapshot(channel_snapshot, clock)
                if report['channels_before_apply']['state'] != 'available':
                    report['state'] = 'baseline_unavailable'
                else:
                    report['state'] = 'ready_to_apply'
            checkpoint()
            if report['state'] != 'ready_to_apply' or should_stop():
                if report['state'] == 'ready_to_apply':
                    report['state'] = 'cancelled_before_apply'
                report['finished_at'] = int(clock())
                checkpoint()
                LOG.info('Otimização: %s; nenhum comando de aplicação enviado.', report['state'])
                return report
            # On any crash from here onward, both ID and global guard prevent replay.
            report['state'] = 'apply_dispatch_uncertain'
            report['apply_command_attempted'] = True
            report['apply_attempted_at'] = int(clock())
            checkpoint()
            if should_stop():
                report['state'] = 'cancelled_before_apply'
                report['apply_command_attempted'] = False
                report['finished_at'] = int(clock())
                checkpoint()
                return report
            LOG.info('Otimização: recomendação positiva; enviando aplicação uma única vez. O Wi-Fi pode interromper temporariamente.')
            started = monotonic()
            try:
                reader.allow_acs_apply = True
                document = json.loads(reader.request(APPLY, timeout_seconds=config['tmp_apply_timeout_seconds']))
                report['apply_ack'] = project_ack(document)
                state = report['apply_ack']['state']
                report['state'] = {'accepted': 'apply_acknowledged', 'rejected': 'apply_rejected',
                                   'unrecognized': 'apply_outcome_uncertain'}[state]
            except Exception:
                report['state'] = 'apply_outcome_uncertain'
                report['apply_ack'] = {'state': 'unavailable'}
            finally:
                reader.allow_acs_apply = False
            report['apply_response_seconds'] = round(monotonic() - started, 3)
            checkpoint()
            # No new TMP calls, login attempts, scans, or writes during observation.
            observe(report, config, channel_snapshot, clock, monotonic, wait, should_stop, checkpoint)
            report['finished_at'] = int(clock())
            checkpoint()
            return report
    except BlockingIOError:
        return {'state': 'action_in_progress'}
    except (OSError, ValueError, TypeError, KeyError):
        # Persisted uncertainty is authoritative. Never repeat the request here.
        return {'state': 'action_storage_or_state_error', 'retry_automatically': False}


def verify_only(options, data, save, channel_snapshot, *, clock=time.time,
                monotonic=time.monotonic, wait=None, should_stop=lambda: False):
    """Resume observation of a journaled application without access to a TMP reader."""
    try:
        with action_lock(data):
            prepared, error = prepare(options, data, clock(), verify=True)
            if error:
                return error
            config, _, request_id, path, journal = prepared
            report = journal['actions'][request_id]['report']
            checkpoint = lambda: persist(path, journal, data, save, report)
            observe(report, config, channel_snapshot, clock, monotonic, wait or default_wait, should_stop, checkpoint)
            report['last_verified_at'] = int(clock())
            checkpoint()
            return {'state': 'verification_only', 'report': report}
    except BlockingIOError:
        return {'state': 'action_in_progress'}
    except (OSError, ValueError, TypeError, KeyError):
        return {'state': 'action_storage_or_state_error', 'retry_automatically': False}
