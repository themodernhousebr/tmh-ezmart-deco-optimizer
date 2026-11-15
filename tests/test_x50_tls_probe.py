"""Validate trust policy and output without connecting to a Deco."""
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import ssl
import struct
import time
import unittest
from unittest.mock import MagicMock, patch
import zlib


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


class ParameterProbeTests(unittest.TestCase):
    PIN = hashlib.sha256(b"public certificate").hexdigest()
    EMAIL = "owner@example.test"

    def response(self):
        return {"error_code": 0, "result": {
            "sub_method": "pake_register", "cipher_suites": 1, "encryption": "aes_128_ccm",
            "iterations": 10000, "dev_random": base64.b64encode(b"r" * 32).decode(),
            "dev_salt": base64.b64encode(b"PRIVATE_SALT").decode(),
            "dev_share": base64.b64encode(b"\x04" + b"x" * 64).decode(),
            "extra_crypt": {"type": "password_shadow", "params": {
                "passwd_id": 5, "rounds": 5000, "salt": "PRIVATE", "shadow": "PRIVATE"}},
            "username": "PRIVATE", "token": "PRIVATE"}}

    def frame(self, message, sequence=b"rrrr", flags=2):
        body = message if isinstance(message, bytes) else json.dumps(message).encode()
        header = b"\x01" + bytes([flags]) + b"\x01\x00" + struct.pack(">I", len(body))
        header += b"\0" * 8 + sequence + bytes.fromhex("92bf30ed")
        return header[:20] + struct.pack(">I", zlib.crc32(header + body)) + body

    def run_fake(self, frame=None, pin=PIN, name="Deco X50(US)"):
        context, raw, tls = MagicMock(), MagicMock(), MagicMock()
        raw.__enter__.return_value = raw
        tls.__enter__.return_value = tls
        context.wrap_socket.return_value = tls
        tls.getpeercert.side_effect = lambda binary_form=False: (
            b"public certificate" if binary_form else {"subject": ((("commonName", name),),)})
        tls.version.return_value = "TLSv1.3"
        tls.cipher.return_value = ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256)
        stream = io.BytesIO(self.frame(self.response()) if frame is None else frame)
        tls.recv.side_effect = lambda size: stream.read(min(7, size))
        with patch.object(probe, "make_context", return_value=context), patch.object(
                probe.socket, "create_connection", return_value=raw), patch.object(
                    probe.secrets, "token_bytes", side_effect=lambda size: b"r" * size):
            result = probe.probe("192.168.68.1", expected_sha256=pin, owner_email=self.EMAIL)
        self.assertNotIn("PRIVATE", str(result))
        self.assertNotIn(self.EMAIL, str(result))
        tls.write.assert_not_called()
        self.assertLessEqual(tls.sendall.call_count, 1)
        return result, tls

    def test_one_negotiation_with_fragmented_reply_and_redaction(self):
        result, tls = self.run_fake()
        self.assertEqual(result["state"], "pake_parameters_received")
        self.assertEqual(result["authentication"], "parameters_only")
        self.assertEqual(result["optimization"], "not_tested")
        self.assertFalse(result["owner_account_verified"])
        self.assertEqual(result["extra_crypt"], {
            "present": True, "type": "password_shadow", "passwd_id": 5, "rounds": 5000})
        tls.sendall.assert_called_once()
        packet = tls.sendall.call_args[0][0]
        self.assertEqual(packet[:4], bytes.fromhex("01010100"))
        self.assertEqual(packet[16:20], b"rrrr")
        self.assertEqual(struct.unpack(">I", packet[4:8])[0], len(packet) - 24)
        self.assertEqual(struct.unpack(">I", packet[20:24])[0],
                         zlib.crc32(packet[:20] + bytes.fromhex("92bf30ed") + packet[24:]))
        request = json.loads(packet[24:])
        self.assertEqual(request["method"], "login")
        self.assertEqual(request["params"]["sub_method"], "pake_register")
        self.assertEqual(request["params"]["passcode_type"], "userpw")
        self.assertEqual(request["params"]["username"], hashlib.sha256(self.EMAIL.encode()).hexdigest())
        self.assertNotIn(self.EMAIL.encode(), packet)
        self.assertNotIn("password", request["params"])

    def test_pin_or_model_mismatch_prevents_any_application_message(self):
        for pin, name in [("0" * 64, "Deco X50(US)"), (self.PIN, "Other")]:
            result, tls = self.run_fake(pin=pin, name=name)
            tls.sendall.assert_not_called()
            self.assertFalse(result["application_send_attempted"])

    def test_parameter_probe_requires_pin_before_network(self):
        with patch.object(probe.socket, "create_connection") as connect:
            with self.assertRaises(ValueError):
                probe.probe("192.168.68.1", owner_email=self.EMAIL)
            connect.assert_not_called()

    def test_header_checksum_sequence_and_truncation_fail_closed(self):
        normal = self.frame(self.response())
        bad_crc = normal[:20] + b"\0" * 4 + normal[24:]
        for frame, reason in [(bad_crc, "checksum_mismatch"),
                              (self.frame(self.response(), sequence=b"xxxx"), "sequence_mismatch"),
                              (self.frame(self.response(), flags=1), "unexpected_header"),
                              (normal[:-8], "response_truncated")]:
            result, _ = self.run_fake(frame)
            self.assertEqual(result["state"], "protocol_error")
            self.assertEqual(result["protocol_error"], reason)

    def test_huge_packet_is_rejected_before_reading_body(self):
        header = self.frame(self.response())[:4] + struct.pack(">I", 2**30)
        header += b"\0" * 8 + b"rrrr" + b"\0" * 4
        result, _ = self.run_fake(header)
        self.assertEqual(result["protocol_error"], "invalid_response_length")

    def test_api_rejection_does_not_retry_or_export_raw_error(self):
        result, tls = self.run_fake(self.frame({"error_code": -2101, "message": "PRIVATE"}))
        self.assertEqual(result["state"], "pake_rejected")
        self.assertEqual(result["api_code"], -2101)
        tls.sendall.assert_called_once()

    def test_malformed_or_unnegotiated_parameters_fail(self):
        for key, value in [("iterations", True), ("dev_random", "invalid"),
                           ("dev_share", base64.b64encode(b"\x03" + b"x" * 64).decode()),
                           ("cipher_suites", 2), ("encryption", "unknown"),
                           ("sub_method", "pake_share")]:
            response = self.response()
            response["result"][key] = value
            result, _ = self.run_fake(self.frame(response))
            self.assertEqual(result["state"], "protocol_error")

    def test_duplicate_keys_are_rejected(self):
        result, _ = self.run_fake(self.frame(b'{"error_code":0,"error_code":-2101}'))
        self.assertEqual(result["protocol_error"], "duplicate_json_key")

    def test_deadline_is_not_reset_for_each_fragment(self):
        connection = MagicMock()
        connection.recv.return_value = b"a"
        with patch.object(probe.time, "monotonic", side_effect=[99, 101]):
            with self.assertRaises(TimeoutError):
                probe.receive_exact(connection, 24, 100)
        connection.recv.assert_called_once()
