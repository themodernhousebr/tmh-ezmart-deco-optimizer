"""Monitoring plus event-driven HA actions. No periodic optimization."""
import ipaddress
import json
import logging
import os
from pathlib import Path
import signal
import threading
import time
from diagnostics import PROBES, probe, error_summary, transient_transport

VERSION = '0.11.0-dev.7'
COOLDOWN = 900
STOP = threading.Event()
LOG = logging.getLogger('ezmart')


def save(path, value):
    temporary = path.with_suffix('.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(value, stream, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def startup_action_enabled(options):
    # Migrating an installed manual optimize configuration must never apply at boot.
    return (options.get('deco_model', 'm9_plus') == 'm9_plus'
            and options.get('tmp_enabled', False) and not options.get('mqtt_enabled', False))


def process_job(controller, client, options, data, store=save, stop=STOP):
    job = controller.take()
    if job is None:
        return None
    if options.get('deco_model', 'm9_plus') == 'x50':
        from x50_backend import run_probe
    else:
        from tmp_readonly import run_probe
    action_options = dict(options, tmp_action=job['action'], tmp_optimize_request_id=job['request_id'])
    def record(path, value):
        store(path, value)
        if path.name in ('tmp_optimize_report.json', 'x50_optimize_report.json'):
            controller.progress(value)
    try:
        guest_args = ({'guest_job': job, 'guest_control': controller.guest}
                      if job['action'].startswith('guest_') else {})
        result = run_probe(action_options, data, record, channel_snapshot=lambda:
                           client.request('admin/wireless', 'wlan', {'operation': 'read'}),
                           should_stop=stop.is_set, wait=stop.wait, **guest_args)
    except Exception:
        result = {'state': 'action_failed_no_replay'}
    controller.finish(result)
    if job['action'].startswith('guest_'):
        result = dict(result, action_type='guest_wifi')
        store(data / 'guest_wifi_report.json', result)
    LOG.info('Pedido Home Assistant concluído: %s', json.dumps(result, ensure_ascii=True))
    return result


def collect(client, options, cycle, disabled, probe_results, probe_times):
    if options.get('deco_model', 'm9_plus') == 'x50':
        LOG.info('Ciclo %s: leitura X50 de modo, inventário e backhaul; sem análise ACS.', cycle)
        return client.collect(cycle, VERSION)
    status = {'updated_at': int(time.time()), 'available': True, 'cycle': cycle, 'version': VERSION}
    status['read_only'] = not (options.get('tmp_enabled', False) and
                               ((options.get('mqtt_enabled', False) and options.get('mqtt_commands_enabled', False)) or
                                (not options.get('mqtt_enabled', False) and options.get('tmp_action') in ('scan', 'optimize'))))
    LOG.info('Ciclo %s iniciado.', cycle)
    for method in ('get_device_list', 'get_device_mode'):
        if method in disabled:
            continue
        try:
            LOG.info('Ciclo %s: consultando %s.', cycle, method)
            result = getattr(client, method)()
        except Exception as error:
            if getattr(error, 'status_code', None) == 404:
                disabled.add(method)
                status[method] = 'unsupported'
                continue
            if transient_transport(error):
                status[method] = {'state': 'error', **error_summary(error)}
                LOG.warning('Ciclo %s: %s indisponível; continuando leituras independentes.', cycle, method)
                continue
            raise
        if method == 'get_device_list':
            status['nodes'] = [{'model': item.device_model, 'firmware': item.software_ver} for item in result]
        else:
            status['mode'] = result.workmode
    for name in PROBES:
        if not (options.get('diagnose_' + name, False) or (options.get('mqtt_enabled', False) and name == 'channels')):
            continue
        every = options.get('diagnostic_seconds', 0)
        if options.get('mqtt_enabled', False):
            every = options.get('poll_seconds', 30) if name == 'channels' else max(60, every)
        if name in probe_times and (every == 0 or time.monotonic() - probe_times[name] < every):
            continue
        probe_times[name] = time.monotonic()
        LOG.info('Diagnóstico %s: leitura iniciada.', name)
        try:
            import guest_wifi
            if name == 'channels' and guest_wifi.enabled(options):
                from diagnostics import project
                probe_results['guest'] = {'state': 'unavailable', 'updated_at': int(time.time())}
                raw = client.request('admin/wireless', 'wlan', {'operation': 'read'})
                probe_results['guest'] = dict(guest_wifi.project(raw), updated_at=int(time.time()))
                projected = project('channels', raw)
                del raw
            else:
                projected = probe(client, name)
            result = {'state': 'available', 'data': projected, 'updated_at': int(time.time())}
        except Exception as error:
            code = getattr(error, 'status_code', None)
            if code in (401, 403) or type(error).__name__ == 'AuthenticationError':
                raise
            result = {'state': 'unsupported' if code == 404 else 'error', 'updated_at': int(time.time())}
            result.update(error_summary(error))
            if code == 404:
                probe_times[name] = float('inf')
        probe_results[name] = result
        LOG.info('Diagnóstico %s: %s', name, json.dumps(result, ensure_ascii=True))
    status['diagnostics'] = dict(probe_results)
    status['unsupported'] = sorted(disabled)
    status['available'] = 'nodes' in status and 'mode' in status
    return status


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    for handler in logging.getLogger().handlers:
        handler.addFilter(lambda record: not record.name.startswith(('paramiko', 'tplink_deco_api', 'paho')))
    from tplink_deco_api import DecoClient
    for name in list(logging.Logger.manager.loggerDict) + ['tplink_deco_api']:
        if name.startswith('tplink_deco_api'):
            logging.getLogger(name).disabled = True
    data = Path('/data')
    try:
        options = json.loads((data / 'options.json').read_text())
        model = options.get('deco_model', 'm9_plus')
        if model not in ('m9_plus', 'x50'):
            raise ValueError()
        host = str(ipaddress.ip_address(options['host']))
        password, username = options.get('password', ''), options.get('username', 'admin')
        interval = options.get('poll_seconds', 30)
        if type(interval) is not int or not 30 <= interval <= 86400:
            raise ValueError()
        if model == 'x50':
            from x50_backend import Client as X50Client, settings, certificate_mode, resolve_certificate
            settings(options, allow_missing_pin=certificate_mode(options) == 'automatic')
            interval = max(60, interval)
        elif not password or not username:
            raise ValueError()
        options['poll_seconds'] = interval
        # Old saved options take precedence. New MQTT installations use the M9 ACS method.
        if options.get('mqtt_enabled', False):
            options.setdefault('tmp_scan_backend', 'm9_acs')
        state = data / 'state.json'
        due = json.loads(state.read_text())['next_login'] if state.exists() else 0
        from m9_actions import finite_time
        if not finite_time(due):
            raise ValueError()
    except Exception:
        LOG.error('Configuração ou estado inválido; revise as opções e reinicie.')
        return 1
    LOG.info('Versão %s; intervalo de consultas: %s segundos.', VERSION, interval)
    if model == 'x50':
        while not STOP.is_set():
            try:
                resolve_certificate(options, data, save)
                LOG.info('X50: certificado individual disponível; cadeia do fabricante e identidade serão verificadas em cada conexão.')
                break
            except Exception:
                LOG.error('X50: não foi possível obter ou carregar o certificado validado; nova tentativa em 60 segundos. Nenhum login enviado.')
                STOP.wait(60)
        if STOP.is_set():
            return 0
    controller = bridge = None
    if options.get('mqtt_enabled', False):
        try:
            from ha_control import Controller
            from mqtt_bridge import Bridge
            controller = Controller(options, data, save)
            bridge = Bridge(options, data, save, controller, STOP, VERSION)
            bridge.start()
            LOG.info('Modo Home Assistant: aguardando pedidos explícitos; ação TMP de inicialização desativada.')
        except Exception:
            LOG.error('Não foi possível iniciar integração MQTT; nenhum comando de otimização será executado.')
            return 1
    cycle = 0
    previous_summary = None
    probe_results, probe_times = {}, {}
    if options.get('diagnose_acs', False):
        probe_results['acs'] = {'state': 'disabled_active_operation', 'updated_at': int(time.time())}
        LOG.info('ACS automático desativado: a operação read pode iniciar varredura.')
    tmp_attempted, client, disabled, next_poll = False, None, set(), 0
    cooldown_logged = False
    try:
        while not STOP.is_set():
            if controller and controller.pending:
                if STOP.is_set():
                    return 0
                result = process_job(controller, client, options, data, stop=STOP)
                if result is not None:
                    if result.get('action_type') != 'guest_wifi':
                        probe_results['tmp'] = {'updated_at': int(time.time()), **result}
                    probe_times.pop('channels', None)
                    # A failed guest connection must not immediately trigger another login/read.
                    next_poll = (time.monotonic() + interval if
                                 result.get('action_type') == 'guest_wifi' and
                                 result.get('state') == 'guest_connection_failed' else 0)
                if STOP.is_set():
                    break
            if client is None:
                remaining = max(0, due - time.time())
                if remaining > 0:
                    if controller:
                        controller.set_monitor({}, False)
                    if not cooldown_logged:
                        LOG.info('Cooldown ativo; próxima autenticação em aproximadamente %s segundos.', int(remaining) + 1)
                        cooldown_logged = True
                    STOP.wait(min(remaining, 1))
                    continue
                cooldown_logged = False
                due = time.time() + COOLDOWN
                save(state, {'next_login': due})
                try:
                    client = (X50Client(options, data, save, STOP) if model == 'x50'
                              else DecoClient(host, username, password, timeout=10))
                    client.login()
                    save(state, {'next_login': 0})
                    due = 0
                    next_poll = 0
                    LOG.info('%s autenticado; monitoramento somente leitura.',
                             'X50 TPAP/TLS' if model == 'x50' else 'HTTPS')
                except Exception as error:
                    if model == 'x50':
                        from x50_backend import AuthenticatedReadUnavailable
                        if isinstance(error, AuthenticatedReadUnavailable):
                            client = None
                            due = time.time() + interval
                            save(state, {'next_login': due})
                            LOG.warning('X50 autenticado, mas leitura incompleta; nova consulta em %s segundos. Nenhum comando enviado.', interval)
                            continue
                    client = None
                    due = time.time() + COOLDOWN
                    save(state, {'next_login': due})
                    LOG.warning('%s indisponível; nova tentativa após 15 minutos.',
                                'Conexão ou leitura X50' if model == 'x50' else 'Autenticação')
                    continue
            try:
                if time.monotonic() >= next_poll:
                    cycle += 1
                    status = collect(client, options, cycle, disabled, probe_results, probe_times)
                    if startup_action_enabled(options) and not tmp_attempted:
                        tmp_attempted = True
                        from tmp_readonly import run_probe
                        result = run_probe(options, data, save, channel_snapshot=lambda:
                                           client.request('admin/wireless', 'wlan', {'operation': 'read'}),
                                           should_stop=STOP.is_set, wait=STOP.wait)
                        probe_results['tmp'] = {'updated_at': int(time.time()), **result}
                        status['diagnostics'] = dict(probe_results)
                        LOG.info('Diagnóstico tmp: %s', json.dumps(probe_results['tmp'], ensure_ascii=True))
                    summary = {key: status[key] for key in ('nodes', 'mode', 'unsupported') if key in status}
                    if summary != previous_summary:
                        LOG.info('Resumo da Mesh: %s', json.dumps(summary, ensure_ascii=True))
                        previous_summary = summary
                    save(data / 'status.json', status)
                    if controller:
                        controller.set_monitor(status, 'nodes' in status and 'mode' in status)
                    LOG.info('Ciclo %s concluído; próxima consulta em %s segundos. Resumo em /data/status.json.', cycle, interval)
                    next_poll = time.monotonic() + interval
            except Exception as error:
                LOG.warning('Falha de consulta: %s', json.dumps(error_summary(error)))
                if controller:
                    controller.set_monitor({}, False)
                save(data / 'status.json', {'updated_at': int(time.time()), 'available': False})
                from x50_backend import AuthenticatedReadUnavailable
                if model == 'x50' and isinstance(error, AuthenticatedReadUnavailable):
                    client = None
                    due = time.time() + interval
                    next_poll = time.monotonic() + interval
                    save(state, {'next_login': due})
                    LOG.warning('X50 autenticado, mas consulta periódica falhou; nova consulta em %s segundos. Nenhum comando reenviado.', interval)
                elif transient_transport(error):
                    next_poll = time.monotonic() + interval
                    LOG.warning('Transporte indisponível; sessão preservada. Nova consulta em %s segundos.', interval)
                else:
                    client = None
                    due = time.time() + COOLDOWN
                    save(state, {'next_login': due})
                    LOG.warning('Sessão descartada; pausa de 15 minutos antes de autenticar.')
            pause = min(1, max(0, next_poll - time.monotonic()))
            if controller:
                controller.wake.wait(pause)
                controller.wake.clear()
            else:
                STOP.wait(pause)
    finally:
        if controller:
            controller.set_monitor({}, False)
        if bridge:
            STOP.set()
            bridge.thread.join(timeout=5)
    return 0


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    raise SystemExit(main())
