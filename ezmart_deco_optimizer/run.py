"""EZmart read-only diagnostics. Never log SDK payloads or exceptions."""
import ipaddress
import json
import logging
import os
from pathlib import Path
import signal
import threading
import time
from diagnostics import PROBES, probe

COOLDOWN = 900
STOP = threading.Event()
LOG = logging.getLogger('ezmart')


def save(path, value):
    temporary = path.with_suffix('.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(value, stream)
    temporary.replace(path)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    logging.getLogger('tplink_deco_api').disabled = True
    # Disable every SDK logger, including children, before constructing a client.
    from tplink_deco_api import DecoClient
    for name in list(logging.Logger.manager.loggerDict):
        if name.startswith('tplink_deco_api'):
            logging.getLogger(name).disabled = True
    data = Path('/data')
    try:
        options = json.loads((data / 'options.json').read_text())
        host = str(ipaddress.ip_address(options['host']))
        password = options['password']
        username = options['username']
        interval = options['poll_seconds']
        if not password or not username or type(interval) is not int or not 30 <= interval <= 86400:
            raise ValueError()
        state = data / 'state.json'
        due = json.loads(state.read_text())['next_login'] if state.exists() else 0
        if not isinstance(due, (int, float)):
            raise ValueError()
    except Exception:
        LOG.error('Configuração ou estado inválido; revise as opções e reinicie.')
        return 1
    LOG.info('Versão 0.3.0-dev.1; intervalo de consultas: %s segundos.', interval)
    cycle = 0
    previous_summary = None
    probe_results = {}
    probe_times = {}
    client = None
    disabled = set()
    while not STOP.is_set():
        if client is None:
            remaining = max(0, due - time.time())
            if remaining > 0:
                LOG.info('Cooldown ativo; próxima autenticação em aproximadamente %s segundos.', int(remaining) + 1)
            if STOP.wait(min(remaining, COOLDOWN)):
                break
            if remaining > COOLDOWN:
                continue
            # Persist before login: restarting cannot bypass the login cooldown.
            due = time.time() + COOLDOWN
            save(state, {'next_login': due})
            try:
                client = DecoClient(host, username, password, timeout=10)
                client.login()
                # Clear the in-flight guard only after confirmed authentication.
                save(state, {'next_login': 0})
                due = 0
                LOG.info('Sessão autenticada; diagnóstico somente leitura.')
            except Exception:
                client = None
                due = time.time() + COOLDOWN
                save(state, {'next_login': due})
                LOG.warning('Autenticação indisponível; nova tentativa após 15 minutos.')
                continue
        try:
            cycle += 1
            LOG.info('Ciclo %s iniciado.', cycle)
            status = {'updated_at': int(time.time()), 'read_only': True, 'available': True, 'cycle': cycle, 'version': '0.3.0-dev.1'}
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
                    raise
                if method == 'get_device_list':
                    status['nodes'] = [
                        {'model': item.device_model, 'firmware': item.software_ver}
                        for item in result
                    ]
                else:
                    status['mode'] = result.workmode
            # Optional probes run serially on the existing session, never in parallel.
            for name in PROBES:
                if not options.get('diagnose_' + name, name == 'acs'):
                    continue
                every = options.get('diagnostic_seconds', 0)
                if name in probe_times and (every == 0 or time.monotonic() - probe_times[name] < every):
                    continue
                probe_times[name] = time.monotonic()
                LOG.info('Diagnóstico %s: leitura iniciada.', name)
                try:
                    result = {'state': 'available', 'data': probe(client, name), 'updated_at': int(time.time())}
                except Exception as error:
                    code = getattr(error, 'status_code', None)
                    if code in (401, 403) or type(error).__name__ == 'AuthenticationError':
                        raise
                    result = {'state': 'unsupported' if code == 404 else 'error', 'updated_at': int(time.time())}
                    if type(code) is int:
                        result['http_status'] = code
                    if code == 404:
                        # Unsupported routes are probed once per process.
                        probe_times[name] = float('inf')
                probe_results[name] = result
                LOG.info('Diagnóstico %s: %s', name, json.dumps(result, ensure_ascii=True))
            status['diagnostics'] = probe_results
            status['unsupported'] = sorted(disabled)
            summary = {key: status[key] for key in ('nodes', 'mode', 'unsupported') if key in status}
            if summary != previous_summary:
                # JSON escaping prevents control characters from becoming log lines.
                LOG.info('Resumo da Mesh: %s', json.dumps(summary, ensure_ascii=True))
                previous_summary = summary

            save(data / 'status.json', status)
            LOG.info('Ciclo %s concluído; próxima consulta em %s segundos. Resumo em /data/status.json.', cycle, interval)
            STOP.wait(interval)
        except Exception:
            # Unknown errors may represent an invalid session: do not retry immediately.
            client = None
            due = time.time() + COOLDOWN
            save(state, {'next_login': due})
            save(data / 'status.json', {'updated_at': int(time.time()), 'available': False})
            LOG.warning('Consulta indisponível; sessão descartada e pausa de 15 minutos.')
    return 0


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    raise SystemExit(main())
