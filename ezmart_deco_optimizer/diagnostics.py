"""Fixed read-only probes and strict public result projections."""
import math

PROBES = {
    'acs': ('admin/network_optimize', 'acs_optimize'),
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
