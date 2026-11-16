"""Fixed read-only probes and strict public result projections."""
import math

PROBES = {
    # acs_optimize/read can start a scan in M9 firmware. Never poll it here.
    'wifi_support': ('admin/wireless', 'get_support'),
    'channels': ('admin/wireless', 'wlan'),
    'performance': ('admin/network', 'performance'),
}
BANDS = ('band2_4', 'band5_1', 'band5_2', 'band6', 'band6_2')


def scalar(value):
    if type(value) in (bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) is str and len(value) <= 40 and all(c.isdigit() or c in '.-_ ' for c in value):
        return value
    return None


def fields(data, names):
    if not isinstance(data, dict):
        return {}
    return {key: scalar(data[key]) for key in names if key in data and scalar(data[key]) is not None}


def project(name, data):
    if not isinstance(data, dict):
        raise ValueError('Unexpected response shape')
    if name == 'acs':
        out = fields(data, ('need_optimize', 'scan_count', 'optimize_count', 'optimize_time'))
        item = data.get('optimize_item', {})
        out['recommendations'] = {}
        if isinstance(item, dict):
            for band in BANDS:
                if isinstance(item.get(band), dict):
                    out['recommendations'][band] = fields(item[band], ('channel_2g', 'channel_5g', 'channel_5g_2', 'channel_6g'))
        return out
    if name == 'performance':
        return fields(data, ('cpu_usage', 'mem_usage'))
    if name == 'wifi_support':
        return {key: value for key, value in data.items()
                if key in ('is_mlo_support', 'is_mlo_band_support', 'is_txpower_support', 'support_dfs')
                and type(value) is bool}
    if name == 'channels':
        out = {}
        for band in BANDS:
            config = data.get(band)
            if not isinstance(config, dict):
                continue
            host = config.get('host', {})
            out[band] = fields(host, ('channel', 'auto_channel'))
            width = host.get('channel_width') if isinstance(host, dict) else None
            if width in ('HT20', 'HT40', 'HT80', 'HT160', 'HT240', 'HT320', '20', '40', '80', '160', '240', '320'):
                out[band]['channel_width'] = width
            out[band]['backhaul'] = fields(config.get('backhaul', {}), ('channel',))
        return out
    raise ValueError('Unknown probe')


def probe(client, name):
    path, form = PROBES[name]
    return project(name, client.request(path, form, {'operation': 'read'}))


def error_summary(error):
    """Expose only fixed categories and numeric codes, never exception text."""
    code = getattr(error, 'status_code', None)
    api_code = getattr(error, 'error_code', None)
    name = type(error).__name__
    categories = {
        'AuthenticationError': 'authentication', 'TransportError': 'transport',
        'ApiError': 'api', 'CryptoError': 'crypto', 'ValueError': 'response_format',
        'TypeError': 'response_format', 'TimeoutError': 'timeout',
    }
    out = {'category': categories.get(name, 'unknown')}
    if type(code) is int:
        out['http_status'] = code
    if type(api_code) is int:
        out['api_code'] = api_code
    return out


def transient_transport(error):
    """Known transport failures don't establish that credentials/session are invalid."""
    if type(error).__name__ in ('TimeoutError', 'ConnectionError'):
        return True
    code = getattr(error, 'status_code', None)
    return type(error).__name__ == 'TransportError' and (code is None or (type(code) is int and 500 <= code <= 599))


def resource_performance(document):
    """X50 device_resource uses integer CPU percent and memory KiB, not ratios."""
    data = document.get('result', {})
    devices = data.get('device_list') if isinstance(data, dict) else None
    if not isinstance(devices, list) or len(devices) != 1 or not isinstance(devices[0], dict):
        return {}
    node = devices[0]
    out = {}
    cpu, used, total = (node.get(k) for k in ('cpu_usage', 'memory_usage', 'memory_total'))
    if type(cpu) in (int, float) and math.isfinite(cpu) and 0 <= cpu <= 100:
        out['cpu_usage'] = cpu / 100
    if (type(used) in (int, float) and type(total) in (int, float)
            and math.isfinite(used) and math.isfinite(total) and 0 <= used <= total and total > 0):
        out['mem_usage'] = used / total
    return out
