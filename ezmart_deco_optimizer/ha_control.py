"""Event-driven action admission. Attempted writes never resume; only explicitly scheduled, unsent guest intent can survive restart."""
from datetime import datetime, timezone
import json
import logging
import threading
import time
import uuid

from m9_actions import finite_time
import m9_actions
import guest_wifi

LOG = logging.getLogger('ezmart')
GUEST_CONFIRMED_STATES = frozenset(('guest_enabled_verified', 'guest_disabled_verified',
                                   'guest_already_set', 'guest_state_confirmed_without_ack'))


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if finite_time(value) and value else None


class Controller:
    def __init__(self, options, data, save, clock=time.time):
        self.options, self.data, self.save, self.clock = options, data, save, clock
        self.x50 = options.get('deco_model', 'm9_plus') == 'x50'
        self.actions = m9_actions
        if self.x50:
            import x50_backend
            self.actions = x50_backend
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.pending = None
        self.scheduled_guest = None
        self.guest_queue_path = data / 'guest_wifi_queue.json'
        self.busy = False
        self.phase = 'idle'
        self.monitor = {}
        self.monitor_at = 0
        self.healthy = False
        self.fault = None
        self.report = {}
        self.active_job = None
        self.guest = guest_wifi.Control(options, data, save, clock)
        self.guest_observed = {}
        self.path = data / 'automation_state.json'
        self.control = {'version': 1, 'next_allowed': 0, 'next_verify': 0,
                        'inflight': False, 'last_request': 'none', 'last_id': '', 'last_action': ''}
        self.cooldown = options.get('request_cooldown_seconds', 600)
        if type(self.cooldown) is not int or not 600 <= self.cooldown <= 86400:
            self.fault = 'invalid_request_cooldown'
        try:
            if self.path.exists():
                saved = json.loads(self.path.read_text())
                if (not isinstance(saved, dict) or saved.get('version') != 1
                        or not all(finite_time(saved.get(k)) for k in ('next_allowed', 'next_verify'))
                        or type(saved.get('inflight')) is not bool
                        or not all(isinstance(saved.get(k), str) for k in ('last_id', 'last_action', 'last_request'))):
                    raise ValueError()
                self.control = saved
                if saved['inflight']:
                    self.control.update(inflight=False, last_request='interrupted_no_replay')
                    save(self.path, self.control)
            self.restore_report()
            # Recalculate only a finished request tied to its durable report.
            started = self.report.get('started_at')
            if (self.path.exists() and not saved['inflight']
                    and self.control['last_request'] != 'interrupted_no_replay'):
                if (self.control['last_action'] == 'optimize' and
                        self.report.get('request_id') == self.control['last_id'] and
                        finite_time(started)):
                    limit = min(self.control['next_allowed'], started + self.cooldown)
                    if limit != self.control['next_allowed']:
                        self.control['next_allowed'] = limit
                        save(self.path, self.control)
        except Exception:
            self.fault = 'invalid_automation_state'
        self.load_guest_queue()

    def save_guest_queue(self, request):
        self.save(self.guest_queue_path, {'version': 1, 'request': request})
        self.scheduled_guest = request

    def load_guest_queue(self):
        try:
            if not self.guest_queue_path.exists():
                return
            document = json.loads(self.guest_queue_path.read_text())
            if document.get('version') != 1:
                raise ValueError()
            request = document.get('request')
            if request is None:
                return
            if (not isinstance(request, dict) or request.get('action') not in ('guest_on', 'guest_off')
                    or not all(finite_time(request.get(k)) for k in ('requested_at', 'not_before', 'expires_at'))
                    or request['requested_at'] > request['not_before']
                    or not 0 < request['expires_at'] - request['not_before'] <= 300):
                raise ValueError()
            if (request.get('target_hash') != guest_wifi.target_hash(self.options)
                    or self.clock() > request['expires_at']
                    or self.guest.state.get('result') not in GUEST_CONFIRMED_STATES):
                self.save_guest_queue(None)
                return
            self.scheduled_guest = request
        except Exception:
            self.fault = 'guest_queue_storage_error'

    def schedule_guest(self, action):
        due = max(self.clock(), self.guest.state['next_allowed'])
        request = {'action': action, 'requested_at': self.clock(), 'not_before': due,
                   'expires_at': due + 300, 'target_hash': guest_wifi.target_hash(self.options)}
        try:
            self.save_guest_queue(request)
        except Exception:
            self.fault = 'guest_queue_storage_error'
            return self.fault
        self.wake.set()
        LOG.info('Pedido de visitantes agendado: %s; execução permitida após %s. Apenas o último pedido será mantido.', action, iso(due))
        return 'scheduled'

    def dispatch_scheduled_guest(self):
        with self.lock:
            request = self.scheduled_guest
            if request is None:
                return
            reason = self.guest_reason()
            temporary = reason in ('busy', 'guest_cooldown', 'tmp_auth_cooldown')
            expired = self.clock() > request['expires_at']
            if expired or (reason != 'ready' and not temporary):
                try:
                    self.save_guest_queue(None)
                except Exception:
                    self.fault = 'guest_queue_storage_error'
                    self.scheduled_guest = None
                LOG.info('Pedido agendado de visitantes cancelado: %s.', 'expired' if expired else reason)
                return
            if temporary or self.clock() < request['not_before']:
                return
            try:
                # Consume durably BEFORE reserving/executing; a crash cannot replay this intent.
                self.save_guest_queue(None)
            except Exception:
                self.fault = 'guest_queue_storage_error'
                self.scheduled_guest = None
                return
            LOG.info('Executando pedido agendado de visitantes: %s.', request['action'])
            return self.accept_guest(request['action'])

    def restore_report(self):
        """Only reports tied to this configured Deco may be published or verified."""
        try:
            target = self.actions.target_hash(self.options)
        except (ValueError, KeyError, TypeError, AttributeError):
            return
        _, journal = self.actions.load_journal(self.data)
        entries = [v['report'] for v in journal['actions'].values() if v['target_hash'] == target]
        if entries:
            self.report = max(entries, key=lambda r: r.get('started_at', 0))

    def set_monitor(self, status, healthy):
        with self.lock:
            self.healthy = healthy
            observed = status.get('diagnostics', {}).get('guest', {})
            if (observed.get('state') == 'available'
                    and observed.get('updated_at', 0) >= self.guest_observed.get('updated_at', 0)):
                self.guest_observed = observed
            self.monitor = status
            self.monitor_at = self.clock() if status else 0

    def reachable(self):
        return self.healthy and self.clock() - self.monitor_at <= max(60, self.options.get('poll_seconds', 30) + 30)

    def verify_id(self):
        try:
            target = self.actions.target_hash(self.options)
            _, journal = self.actions.load_journal(self.data)
            reports = [v['report'] for v in journal['actions'].values()
                       if v['target_hash'] == target and v['report']['apply_command_attempted']]
            if reports:
                return max(reports, key=lambda r: r['apply_attempted_at'])['request_id']
        except Exception:
            pass
        return None

    def reason(self, action='optimize'):
        if self.fault:
            return self.fault
        if not self.options.get('mqtt_commands_enabled', False) or not self.options.get('tmp_enabled', False):
            return 'commands_disabled'
        if self.busy or self.pending:
            return 'busy'
        if self.x50 and not self.reachable():
            return 'monitor_unavailable'
        if guest_wifi.enabled(self.options) and self.clock() < self.guest.state['next_allowed']:
            return 'guest_cooldown'
        if self.x50 and not self.monitor.get('profile_supported', False):
            return 'x50_profile_not_validated'
        if self.clock() < self.control['next_allowed' if action == 'optimize' else 'next_verify']:
            return 'request_cooldown'
        if action == 'optimize' or self.x50:
            try:
                path = self.data / ('x50_auth_state.json' if self.x50 else 'tmp_state.json')
                due = json.loads(path.read_text()).get('next_login', 0) if path.exists() else 0
                if not finite_time(due):
                    return 'invalid_tmp_state'
                if self.clock() < due:
                    return 'tmp_auth_cooldown'
            except Exception:
                return 'invalid_tmp_state'
        request_id = 'ha-' + uuid.uuid4().hex if action == 'optimize' else self.verify_id()
        if not request_id:
            return 'nothing_to_verify'
        options = dict(self.options, tmp_optimize_request_id=request_id)
        _, error = self.actions.prepare(options, self.data, self.clock(), verify=action == 'verify')
        return error['state'] if error else 'ready'

    def guest_snapshot(self):
        current = self.monitor.get('diagnostics', {}).get('guest', {})
        return self.guest_observed if self.guest_observed.get('updated_at', 0) > current.get('updated_at', 0) else current

    def guest_readable(self):
        # Only a current WLAN observation can enable guest control, even when
        # inventory failed. An authentication/global failure clears monitor.
        observed = self.monitor.get('diagnostics', {}).get('guest', {})
        return (observed.get('state') == 'available'
                and self.clock() - observed.get('updated_at', 0)
                <= max(60, self.options.get('poll_seconds', 30) + 30))

    def guest_reason(self):
        if not guest_wifi.enabled(self.options):
            return 'guest_control_disabled'
        if self.fault:
            return self.fault
        if (not self.options.get('mqtt_enabled') or not self.options.get('mqtt_commands_enabled')
                or not self.options.get('tmp_enabled')):
            return 'commands_disabled'
        if self.busy or self.pending:
            return 'busy'
        try:
            path = self.data / ('x50_auth_state.json' if self.x50 else 'tmp_state.json')
            due = json.loads(path.read_text()).get('next_login', 0) if path.exists() else 0
            if not finite_time(due):
                return 'invalid_tmp_state'
            if self.clock() < due:
                return 'tmp_auth_cooldown'
        except Exception:
            return 'invalid_tmp_state'
        return self.guest.reason()

    def accept_guest(self, action):
        reason = self.guest_reason()
        active_guest = self.active_job or self.pending or {}
        if reason == 'guest_cooldown' or (reason == 'busy' and active_guest.get('action', '').startswith('guest_')):
            return self.schedule_guest(action)
        if reason != 'ready':
            LOG.info('Pedido de visitantes não executado: %s.', reason)
            return reason
        request_id = 'guest-' + uuid.uuid4().hex
        try:
            if self.scheduled_guest is not None:
                self.save_guest_queue(None)
            self.guest.reserve(request_id)
        except Exception:
            self.fault = 'guest_storage_error'
            return self.fault
        self.pending = {'action': action, 'request_id': request_id, 'expires_at': self.clock() + 60}
        self.wake.set()
        LOG.info('Pedido de visitantes aceito: %s.', action)
        return 'accepted'

    def accept(self, action):
        with self.lock:
            if action in ('guest_on', 'guest_off'):
                return self.accept_guest(action)
            if action not in ('optimize', 'verify'):
                return 'invalid_command'
            reason = self.reason(action)
            if reason != 'ready':
                self.control['last_request'] = reason
                LOG.info('Pedido Home Assistant não executado: %s.', reason)
                return reason
            request_id = 'ha-' + uuid.uuid4().hex if action == 'optimize' else self.verify_id()
            now = self.clock()
            self.control.update(inflight=True, last_request='accepted', last_id=request_id,
                                last_action=action, requested_at=now)
            self.control['next_allowed' if action == 'optimize' else 'next_verify'] = now + (self.cooldown if action == 'optimize' else 30)
            try:
                self.save(self.path, self.control)
            except Exception:
                self.fault = 'automation_storage_error'
                return self.fault
            self.pending = {'action': action, 'request_id': request_id, 'expires_at': now + 60}
            self.wake.set()
            LOG.info('Pedido Home Assistant aceito: %s; ID %s.', action, request_id)
            return 'accepted'

    def take(self):
        with self.lock:
            job, self.pending = self.pending, None
            if not job:
                return None
            self.active_job = job
            if self.clock() > job['expires_at'] or (self.x50 and not job['action'].startswith('guest_') and not self.reachable()):
                self.finish({'state': 'request_expired'})
                return None
            self.busy = True
            self.phase = 'starting'
            if not job['action'].startswith('guest_'):
                self.control['last_request'] = 'running'
            return job

    def progress(self, report):
        with self.lock:
            # The source is the already sanitized, durable backend report.
            self.report = json.loads(json.dumps(report))
            self.phase = report.get('state', 'running')

    def finish(self, result):
        with self.lock:
            if self.active_job and self.active_job['action'].startswith('guest_'):
                self.busy = False
                self.phase = 'idle'
                self.active_job = None
                if result.get('observed'):
                    self.guest_observed = result['observed']
                try:
                    self.guest.finish(result)
                    if (self.scheduled_guest is not None and result.get('state') not in GUEST_CONFIRMED_STATES):
                        self.save_guest_queue(None)
                        LOG.info('Pedido agendado de visitantes cancelado após falha ou resultado incerto; nenhum reenvio automático.')
                except Exception:
                    self.fault = 'guest_storage_error'
                return
            self.active_job = None
            report = result.get('report', result)
            if report.get('request_id'):
                self.progress(report)
            self.busy = False
            self.phase = 'idle'
            self.control.update(inflight=False, last_request=result.get('state', 'action_failed'))
            try:
                self.save(self.path, self.control)
            except Exception:
                self.fault = 'automation_storage_error'

    def view(self):
        """Fixed public fields; never publish options, tokens or SDK documents."""
        with self.lock:
            r = self.report
            verification = r.get('verification', {})
            preflight = r.get('preflight', r.get('scan', {}))
            comparison = verification.get('channel_comparison', verification)
            channels = self.monitor.get('diagnostics', {}).get('channels', {})
            observed = verification.get('last_available', r.get('channels_before_apply', {}))
            if self.x50 and observed:
                at = (verification.get('last_available_at', verification.get('finished_at', 0))
                      if verification.get('last_available') else r.get('apply_attempted_at', 0))
                observed = dict(observed, updated_at=at)
            if observed.get('updated_at', 0) > channels.get('updated_at', 0):
                channels = observed
            bands = channels.get('data', {}) if channels.get('state') == 'available' else {}
            perf = self.monitor.get('diagnostics', {}).get('performance', {}).get('data', {})
            reason = self.reason()
            state = r.get('state', 'idle')
            if self.busy and self.phase.startswith('apply_') and verification:
                state = 'verifying'
            elif self.pending:
                state = 'pending'
            elif self.busy and self.phase in ('starting', 'preflight_reserved'):
                state = 'verifying' if self.control['last_action'] == 'verify' else 'analyzing'
            if self.active_job and self.active_job['action'].startswith('guest_'):
                state = 'guest_switching'
            guest = self.guest_snapshot()
            guest_reason = self.guest_reason()
            out = {
                'ready': reason == 'ready', 'verify_ready': self.reason('verify') == 'ready',
                'blocked_reason': reason, 'busy': self.busy or self.pending is not None,
                'reachable': self.reachable(), 'state': state,
                'result': r.get('conclusion', r.get('state', 'none')),
                'last_request': self.control['last_request'],
                'last_action_at': iso(r.get('finished_at', r.get('started_at', 0))),
                'next_allowed_at': iso(self.control['next_allowed']),
                'monitor_at': iso(self.monitor_at),
                'node_count': len(self.monitor['nodes']) if 'nodes' in self.monitor else None,
                'mode': self.monitor.get('mode', 'unknown'),
                'need_optimize': preflight.get('need_optimize'),
                'channels_changed': comparison.get('observed_channels_changed'),
                'channels_at': iso(channels.get('updated_at', 0)),
                'performance_at': iso(self.monitor.get('diagnostics', {}).get('performance', {}).get('updated_at', 0)),
                'guest_enabled': guest.get('enabled'),
                'commands_available': (not self.fault and self.options.get('tmp_enabled', False)
                                       and self.options.get('mqtt_commands_enabled', False)),
                'guest_available': (guest_wifi.enabled(self.options) and not self.fault
                                    and self.options.get('tmp_enabled', False)
                                    and self.options.get('mqtt_commands_enabled', False)),
                'guest_details': {'observation_fresh': self.guest_readable(), 'bands': guest.get('bands', {}), 'observed_at': iso(guest.get('updated_at', 0)),
                                  'result': self.guest.state['result'], 'blocked_reason': guest_reason,
                                  'verification_scope': 'main_deco', 'request_id': self.guest.state['request_id'],
                                  'write_attempted': self.guest.state['write_attempted'],
                                  'next_allowed_at': iso(self.guest.state['next_allowed']),
                                  'scheduled_action': self.scheduled_guest.get('action') if self.scheduled_guest else None,
                                  'scheduled_for': iso(max(self.scheduled_guest['not_before'], self.guest.state['next_allowed'])) if self.scheduled_guest else None,
                                  'scheduled_expires_at': iso(self.scheduled_guest['expires_at']) if self.scheduled_guest else None},
                'details': {'request_id': r.get('request_id'), 'state': r.get('state'),
                            'last_request_id': self.control['last_id'], 'last_requested_action': self.control['last_action'],
                            'verification': verification.get('state'), 'api_code': r.get('apply_ack', {}).get('api_code'),
                            'channel_changes': comparison.get('channel_changes', []),
                            'next_allowed_at': iso(self.control['next_allowed']), 'blocked_reason': reason,
                            'channels_observed_at': iso(channels.get('updated_at', 0))},
            }
            for band in ('band2_4', 'band5_1', 'band5_2'):
                values = bands.get(band, {})
                out[band] = (values.get('backhaul', {}) if self.x50 else values).get('channel')
            if self.x50:
                out['details'].update(channel_scope='backhaul', radio_changes=verification.get('radio_changes', []))
            for key in ('cpu_usage', 'mem_usage'):
                value = perf.get(key)
                out[key] = round(value * 100, 1) if type(value) in (int, float) and 0 <= value <= 1 else None
            return out
