"""Event-driven action admission. A restart never resumes a write or a queue."""
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
        self.cooldown = options.get('request_cooldown_seconds', 900)
        if type(self.cooldown) is not int or not 900 <= self.cooldown <= 86400:
            self.fault = 'invalid_request_cooldown'
        elif self.x50:
            self.cooldown = max(1800, self.cooldown)
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
        except Exception:
            self.fault = 'invalid_automation_state'

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
        if not self.reachable():
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
        observed = self.guest_snapshot()
        if (not self.guest_readable() or observed.get('state') != 'available'
                or self.clock() - observed.get('updated_at', 0) > max(60, self.options.get('poll_seconds', 30) + 30)):
            return 'guest_monitor_unavailable'
        try:
            path = self.data / 'tmp_state.json'
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
        if reason != 'ready':
            LOG.info('Pedido de visitantes não executado: %s.', reason)
            return reason
        request_id = 'guest-' + uuid.uuid4().hex
        try:
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
            if self.clock() > job['expires_at'] or not self.reachable():
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
                'guest_available': (guest_wifi.enabled(self.options) and self.guest_readable()
                                    and self.options.get('tmp_enabled', False) and self.options.get('mqtt_commands_enabled', False)
                                    and guest.get('state') == 'available'
                                    and self.clock() - guest.get('updated_at', 0) <= max(60, self.options.get('poll_seconds', 30) + 30)),
                'guest_details': {'bands': guest.get('bands', {}), 'observed_at': iso(guest.get('updated_at', 0)),
                                  'result': self.guest.state['result'], 'blocked_reason': guest_reason,
                                  'verification_scope': 'main_deco', 'request_id': self.guest.state['request_id'],
                                  'write_attempted': self.guest.state['write_attempted'],
                                  'next_allowed_at': iso(self.guest.state['next_allowed'])},
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
