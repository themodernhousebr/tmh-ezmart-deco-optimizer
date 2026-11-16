"""Deco guest-only control: explicit commands, one write, observed state, no replay."""
import json
import re
import time

from m9_actions import action_lock, finite_time
import m9_actions

BANDS = ('band2_4', 'band5_1')
READ_DEVICES = 0x400F
READ_MODE = 0x40A0
WRITE_WLAN = 0x4008
READ_WLAN = 0x4009
READ_OPS = (READ_DEVICES, READ_MODE, READ_WLAN)
FIRMWARE = '1.9.1 Build 20250612 Rel. 45690'


def enabled(options):
    return options.get('deco_model', 'm9_plus') in ('m9_plus', 'x50') and options.get('guest_control_enabled') is True


def target_hash(options):
    if options.get('deco_model') == 'x50':
        from x50_backend import target_hash as target
        return target(options)
    return m9_actions.target_hash(options)


def project(raw):
    """Only boolean state leaves the worker; SSIDs/passwords stay in memory."""
    bands = {}
    if isinstance(raw, dict):
        for band in BANDS:
            config = raw.get(band, {})
            guest = config.get('guest', {}) if isinstance(config, dict) else {}
            value = guest.get('enable') if isinstance(guest, dict) else None
            if type(value) is bool:
                bands[band] = value
    complete = len(bands) == len(BANDS)
    return {'state': 'available' if complete else 'unavailable', 'bands': bands,
            'enabled': any(bands.values()) if complete else None}


def protected_config(raw):
    """Compare credentials/security locally, never serialize the comparison data."""
    if project(raw)['state'] != 'available':
        raise ValueError()
    protected = {}
    for band in BANDS:
        config, guest = raw[band], raw[band]['guest']
        host = config.get('host')
        if (not isinstance(host, dict) or not isinstance(host.get('ssid'), str)
                or not isinstance(host.get('password'), str)
                or not isinstance(guest.get('ssid'), str) or not guest['ssid']
                or not isinstance(guest.get('password'), str)
                or type(guest.get('host_isolation')) is not bool):
            raise ValueError()
    for band, config in raw.items():
        if band.startswith('band') and isinstance(config, dict):
            # Radio channels, backhaul and guest expiry counters can change normally.
            protected[band] = {key: value for key, value in config.items() if key in ('host', 'iot')}
            if isinstance(config.get('guest'), dict):
                protected[band]['guest'] = {key: value for key, value in config['guest'].items()
                                           if key not in ('enable', 'remain_time', 'remaining_time', 'remaining_seconds', 'start_time')}
    return protected


def write_params(raw, desired):
    if type(desired) is not bool:
        raise ValueError()
    protected_config(raw)
    isolation = [raw[band]['guest']['host_isolation'] for band in BANDS]
    if isolation[0] != isolation[1]:
        raise ValueError()
    # Partial update: never send host, SSID, password, encryption, VLAN or timer.
    return {band: {'guest': {'enable': desired, 'host_isolation': isolation[0]}} for band in BANDS}


def validate_params(params):
    if not isinstance(params, dict) or set(params) != set(BANDS):
        raise ValueError()
    values = []
    for band in BANDS:
        config = params[band]
        if not isinstance(config, dict) or set(config) != {'guest'}:
            raise ValueError()
        guest = config['guest']
        if (not isinstance(guest, dict) or set(guest) != {'enable', 'host_isolation'}
                or any(type(v) is not bool for v in guest.values())):
            raise ValueError()
        values.append(guest)
    if values[0] != values[1]:
        raise ValueError()


def read_result(reader, opcode):
    document = json.loads(reader.guest_request(opcode))
    if (not isinstance(document, dict) or type(document.get('error_code')) is not int
            or document['error_code'] != 0 or not isinstance(document.get('result'), dict)):
        raise ValueError()
    return document['result']


def supported_profile(reader, model="m9_plus"):
    devices = read_result(reader, READ_DEVICES).get('device_list')
    mode = read_result(reader, READ_MODE).get('mode')
    if not isinstance(devices, list):
        return False
    masters = [item for item in devices if isinstance(item, dict) and item.get('role') == 'master']
    if len(masters) != 1:
        return False
    master = masters[0]
    if model == 'x50':
        import x50_tpap
        return x50_tpap.supported_profile(master.get('device_model'), master.get('hardware_ver'),
                                          master.get('software_ver'), mode)
    return (master.get('device_model') == 'M9Plus'
            and re.fullmatch(r'2(?:\.[0-9]{1,2}){1,2}', str(master.get('hardware_ver', ''))) is not None
            and master.get('software_ver') == FIRMWARE and mode in ('AP', 'FAP'))


def optimization_block(options, data, now):
    """Guest writes must not overlap an unverified/recent channel application."""
    try:
        backend = m9_actions
        if options.get('deco_model') == 'x50':
            import x50_backend
            backend = x50_backend
        _, journal = backend.load_journal(data)
        target = target_hash(options)
        for entry in journal['actions'].values():
            r = entry['report']
            if entry['target_hash'] == target and r['apply_command_attempted']:
                if r.get('verification', {}).get('state') != 'stable':
                    return 'previous_apply_unverified'
    except Exception:
        return 'invalid_optimize_journal'
    return None


class Control:
    """Guest journal is independent from optimization IDs/results and survives boot."""
    def __init__(self, options, data, save, clock=time.time):
        self.options, self.data, self.save, self.clock = options, data, save, clock
        self.path = data / 'guest_wifi_state.json'
        self.fault = None
        self.state = {'version': 1, 'next_allowed': 0, 'inflight': False, 'write_attempted': False,
                      'request_id': '', 'result': 'none'}
        try:
            if self.path.exists():
                saved = json.loads(self.path.read_text())
                if (saved.get('version') != 1 or not finite_time(saved.get('next_allowed'))
                        or any(type(saved.get(k)) is not bool for k in ('inflight', 'write_attempted'))
                        or not all(isinstance(saved.get(k), str) for k in ('request_id', 'result', 'target_hash'))):
                    raise ValueError()
                if saved['target_hash'] == target_hash(options):
                    self.state = saved
                    if saved['inflight']:
                        self.state.update(inflight=False, result='interrupted_no_replay')
                        self.persist()
        except Exception:
            self.fault = 'invalid_guest_state'

    def persist(self):
        self.save(self.path, self.state)

    def reason(self):
        if not enabled(self.options):
            return 'guest_control_disabled'
        if self.fault:
            return self.fault
        if self.clock() < self.state['next_allowed']:
            return 'guest_cooldown'
        return optimization_block(self.options, self.data, self.clock()) or 'ready'

    def reserve(self, request_id):
        self.state.update(inflight=True, write_attempted=False, request_id=request_id,
                          target_hash=target_hash(self.options), result='accepted', next_allowed=self.clock() + 60)
        self.persist()

    def reserve_write(self, request_id):
        saved = json.loads(self.path.read_text())
        if (self.state['request_id'] != request_id or not self.state['inflight']
                or self.state['write_attempted'] or saved.get('request_id') != request_id
                or saved.get('target_hash') != target_hash(self.options)
                or saved.get('inflight') is not True or saved.get('write_attempted') is not False):
            raise ValueError()
        self.state.update(write_attempted=True, result='dispatch_uncertain', next_allowed=self.clock() + 60)
        self.persist()  # Storage failure prevents sending, never retries a write.

    def finish(self, result):
        self.state.update(inflight=False, result=result.get('state', 'guest_failed_no_replay'), finished_at=self.clock())
        self.persist()


def execute(reader, options, data, control, job, should_stop=lambda: False,
            wait=None, clock=time.time):
    """A single explicit guest write followed by at most twelve passive observations."""
    if (not enabled(options) or not options.get('mqtt_enabled')
            or not options.get('mqtt_commands_enabled') or not options.get('tmp_enabled')):
        return {'state': 'guest_control_disabled'}
    desired = job.get('action') == 'guest_on'
    if job.get('action') not in ('guest_on', 'guest_off'):
        return {'state': 'invalid_guest_command'}
    wait = wait or (lambda seconds: time.sleep(seconds))
    report = {'state': 'guest_preflight_failed', 'write_attempted': False, 'acknowledged': False,
              'request_id': job.get('request_id'), 'requested_enabled': desired}
    with action_lock(data):
        block = optimization_block(options, data, clock())
        if block:
            return {'state': block}
        if not supported_profile(reader, options.get('deco_model', 'm9_plus')):
            return {'state': 'guest_profile_not_validated'}
        try:
            # The HTTPS admin/wireless view omits host_isolation. Use the full
            # mobile-app WLAN view, on this pinned/authenticated TMP session,
            # for both the preservation baseline and all post-write checks.
            raw = read_result(reader, READ_WLAN)
        except Exception:
            return dict(report, state='guest_configuration_unavailable', stage='configuration_read')
        try:
            baseline = protected_config(raw)
            params = write_params(raw, desired)
        except Exception:
            return dict(report, state='guest_configuration_unavailable', stage='configuration_validation')
        before = project(raw)
        report['observed'] = dict(before, updated_at=clock())
        if all(value == desired for value in before['bands'].values()):
            report['state'] = 'guest_already_set'
            return report
        if should_stop():
            return {'state': 'guest_cancelled'}
        control.reserve_write(job['request_id'])
        report['write_attempted'] = True
        try:
            ack = json.loads(reader.guest_request(WRITE_WLAN, params=params))
            code = ack.get('error_code') if isinstance(ack, dict) else None
            if type(code) is int:
                report['api_code'] = code
                report['acknowledged'] = code == 0
        except Exception:
            pass  # A lost acknowledgement never triggers a second write.
        report['state'] = 'guest_outcome_uncertain'
        stable = 0
        for _ in range(12):
            if should_stop() or wait(5):
                break
            try:
                after = read_result(reader, READ_WLAN)
                observed = project(after)
                if observed['state'] != 'available':
                    stable = 0
                    continue
                report['observed'] = dict(observed, updated_at=clock())
                if protected_config(after) != baseline:
                    report['state'] = 'guest_settings_changed'
                    break
                stable = stable + 1 if all(v == desired for v in observed['bands'].values()) else 0
                if stable >= 2:
                    report['state'] = ('guest_enabled_verified' if desired else 'guest_disabled_verified')
                    if not report['acknowledged']:
                        report['state'] = 'guest_state_confirmed_without_ack'
                    break
            except Exception:
                stable = 0
        return report
