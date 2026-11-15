"""Validate trust policy and output without connecting to a Deco."""
import hashlib
import importlib.util
from pathlib import Path
import ssl
import unittest
from unittest.mock import MagicMock, patch


spec = importlib.util.spec_from_file_location(
    "x50_tls_probe", Path(__file__).resolve().parents[1] / "tools/x50_tls_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class TLSProbeTests(unittest.TestCase):
    def test_only_firmware_public_ca_is_trusted(self):
        context = probe.make_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertFalse(context.check_hostname)
        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
        roots = context.get_ca_certs(binary_form=True)
        self.assertEqual(len(roots), 1)
        self.assertEqual(hashlib.sha256(roots[0]).hexdigest(),
                         "fa85d87d218313286f0ef67628d177a1270a446dbd1554b7d47d86aee2fdb216")

    def run_fake(self, error=None, name="Deco X50(US)", pin=None):
        raw, tls, context = MagicMock(), MagicMock(), MagicMock()
        raw.__enter__.return_value = raw
        tls.__enter__.return_value = tls
        context.wrap_socket.return_value = tls
        if error:
            context.wrap_socket.side_effect = error
        tls.getpeercert.side_effect = lambda binary_form=False: (
            b"public certificate" if binary_form else {
                "subject": ((("commonName", name), ("serialNumber", "PRIVATE")),),
                "subjectAltName": (("DNS", "PRIVATE"),)})
        tls.version.return_value = "TLSv1.3"
        tls.cipher.return_value = ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256)
        with patch.object(probe, "make_context", return_value=context), patch.object(
                probe.socket, "create_connection", return_value=raw) as connect:
            result = probe.probe("192.168.68.1", expected_sha256=pin)
        connect.assert_called_once_with(("192.168.68.1", 30001), timeout=8)
        self.assertEqual(context.wrap_socket.call_count, 1)
        for connection in (raw, tls):
            connection.send.assert_not_called()
            connection.sendall.assert_not_called()
            connection.write.assert_not_called()
        self.assertNotIn("PRIVATE", str(result))
        return result

    def test_verified_chain_is_not_authentication_or_device_pin(self):
        result = self.run_fake()
        self.assertEqual(result["state"], "tls_verified")
        self.assertTrue(result["vendor_chain_verified"])
        self.assertFalse(result["hostname_verified"])
        self.assertIsNone(result["certificate_pin_matched"])
        self.assertEqual(result["authentication"], "not_tested")
        self.assertEqual(result["optimization"], "not_tested")

    def test_pin_match_and_mismatch(self):
        pin = hashlib.sha256(b"public certificate").hexdigest()
        self.assertTrue(self.run_fake(pin=pin)["certificate_pin_matched"])
        self.assertEqual(self.run_fake(pin="0" * 64)["state"], "certificate_pin_mismatch")

    def test_wrong_product_does_not_pass_or_disclose_name(self):
        result = self.run_fake(name="PRIVATE")
        self.assertEqual(result["state"], "unexpected_certificate_model")

    def test_untrusted_chain_does_not_retry_insecurely(self):
        error = ssl.SSLCertVerificationError(1, "PRIVATE")
        error.verify_code = 20
        result = self.run_fake(error=error)
        self.assertEqual(result["state"], "certificate_untrusted")
        self.assertFalse(result["vendor_chain_verified"])
        self.assertNotIn("certificate_sha256", result)
        self.assertEqual(result["verify_code"], 20)

    def test_timeout_and_protocol_failure_are_distinct(self):
        self.assertEqual(self.run_fake(error=TimeoutError())["state"], "timeout")
        self.assertEqual(self.run_fake(error=ssl.SSLError("PRIVATE"))["state"], "tls_error")

    def test_refused_connection_does_not_try_m9_or_other_ports(self):
        with patch.object(probe.socket, "create_connection", side_effect=ConnectionRefusedError()) as connect:
            self.assertEqual(probe.probe("192.168.68.1")["state"], "connection_refused")
        self.assertEqual(connect.call_count, 1)

    def test_invalid_inputs_do_not_connect(self):
        for host, timeout, pin in [("deco.example", 8, None), ("0.0.0.0", 8, None),
                                   ("224.0.0.1", 8, None), ("192.168.68.1", 0, None),
                                   ("192.168.68.1", 31, None), ("192.168.68.1", 8, "bad")]:
            with patch.object(probe.socket, "create_connection") as connect:
                with self.assertRaises(ValueError):
                    probe.probe(host, timeout, pin)
                connect.assert_not_called()
