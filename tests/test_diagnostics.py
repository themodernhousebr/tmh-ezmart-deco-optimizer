import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / 'ezmart_deco_optimizer' / 'diagnostics.py'
spec = importlib.util.spec_from_file_location('diagnostics', path)
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)

class DiagnosticsTests(unittest.TestCase):
    def test_wifi_secrets_and_identifiers_never_exported(self):
        result = d.project('channels', {'password': 'secret', 'band2_4': {
            'host': {'ssid': 'private', 'password': 'secret', 'channel': '6', 'channel_width': 'HT20'},
            'guest': {'password': 'guest-secret'}, 'backhaul': {'channel': 44, 'mac': 'AA:BB'}}})
        self.assertEqual(result, {'band2_4': {'channel': '6', 'channel_width': 'HT20', 'backhaul': {'channel': 44}}})

    def test_arbitrary_nested_acs_data_is_dropped(self):
        result = d.project('acs', {'need_optimize': True, 'stok': 'token',
            'scan_count': '3', 'optimize_time': {'password': 'secret'},
            'optimize_item': {'band2_4': {'channel_2g': 6, 'ssid': 'private'}}})
        self.assertEqual(result, {'need_optimize': True, 'scan_count': '3',
            'recommendations': {'band2_4': {'channel_2g': 6}}})

    def test_probes_only_send_fixed_reads(self):
        class Fake:
            def request(self, path, form, payload):
                self.call = (path, form, payload)
                return {}
        client = Fake()
        for name, route in d.PROBES.items():
            d.probe(client, name)
            self.assertEqual(client.call, (*route, {'operation': 'read'}))

    def test_error_summary_never_exposes_exception_text(self):
        class ApiError(Exception):
            error_code = -1
        error = ApiError('password=secret stok=private')
        self.assertEqual(d.error_summary(error), {'category': 'api', 'api_code': -1})
        self.assertEqual(d.error_summary(RuntimeError('secret')), {'category': 'unknown'})

    def test_malformed_response_and_nonfinite(self):
        with self.assertRaises(ValueError):
            d.project('acs', ['unexpected'])
        self.assertEqual(d.project('performance', {'cpu_usage': float('nan'), 'mem_usage': 0.4}), {'mem_usage': 0.4})

if __name__ == '__main__':
    unittest.main()
