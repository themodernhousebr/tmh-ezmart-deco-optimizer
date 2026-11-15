"""Validate trust policy and output without connecting to a Deco."""
import base64
import hashlib
import hmac
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


class AuthenticationProbeTests(unittest.TestCase):
    EMAIL = "Owner@example.test"
    PASSWORD = " client-specific password "
    PIN = ParameterProbeTests.PIN

    def test_published_rfc9383_p256_sha256_vector(self):
        # RFC 9383 Appendix C, first vector. Fixed public test values only.
        # Checks both point arithmetic and the full transcript/HKDF/HMAC chain.
        context = b"SPAKE2+-P256-SHA256-HKDF-SHA256-HMAC-SHA256 Test Vectors"
        w0 = int("bb8e1bbcf3c48f62c08db243652ae55d3e5586053fca77102994f23ad95491b3", 16)
        w1 = int("7e945f34d78785b8a3ef44d0df5a1a97d6b3b460409a345ca7830387a74b1dba", 16)
        scalar = int("d1232c8e8693d02368976c174e2088851b8365d0d79a9eee709c6a05a2fad539", 16)
        server = bytes.fromhex(
            "04c0f65da0d11927bdf5d560c69e1d7d939a05b0e88291887d679fcadea75810fb5"
            "cc1ca7494db39e82ff2f50665255d76173e09986ab46742c798a9a68437b048")
        share, own, peer = probe._spake2p_proofs(context, w0, w1, scalar, server, b"client", b"server")
        self.assertEqual(share.hex(),
                         "04ef3bd051bf78a2234ec0df197f7828060fe9856503579bb1733009042c15c0c1d"
                         "e127727f418b5966afadfdd95a6e4591d171056b333dab97a79c7193e341727")
        self.assertEqual(own.hex(), "926cc713504b9b4d76c9162ded04b5493e89109f6d89462cd33adc46fda27527")
        self.assertEqual(peer.hex(), "9747bcc4f8fe9f63defee53ac9b07876d907d55047e6ff2def2e7529089d3e68")

    def run_server(self, password=PASSWORD, change_params=None, change_reply=None,
                   pin=PIN, send_error=None):
        """Independent verifier role, replying to each actual framed request.

        Never uses the production prover/transcript helpers to build its proof.
        This fake lives entirely in memory and opens no listening socket.
        """
        from Crypto.PublicKey import ECC
        from Crypto.Protocol.KDF import HKDF, PBKDF2
        from Crypto.Hash import SHA256

        def encode(point):
            return b"\x04" + int(point.x).to_bytes(32, "big") + int(point.y).to_bytes(32, "big")

        salt, server_random = b"s" * 32, b"d" * 32
        derived = PBKDF2((self.EMAIL + "/" + self.PASSWORD).encode(), salt, 80,
                         count=1000, hmac_hash_module=SHA256)
        w0 = int.from_bytes(derived[:40], "big") % probe.P256_ORDER
        w1 = int.from_bytes(derived[40:], "big") % probe.P256_ORDER
        m = ECC.import_key(probe.SPAKE_M, curve_name="P-256").pointQ
        n = ECC.import_key(probe.SPAKE_N, curve_name="P-256").pointQ
        generator = ECC.construct(curve="P-256", d=1).pointQ
        y = 123456789
        server_share = encode(generator * y + n * w0)
        params = {"sub_method": "pake_register", "cipher_suites": 1,
                  "encryption": "aes_128_ccm", "iterations": 1000,
                  "dev_random": base64.b64encode(server_random).decode(),
                  "dev_salt": base64.b64encode(salt).decode(),
                  "dev_share": base64.b64encode(server_share).decode()}
        context, raw, tls = MagicMock(), MagicMock(), MagicMock()
        raw.__enter__.return_value = raw
        tls.__enter__.return_value = tls
        context.wrap_socket.return_value = tls
        tls.getpeercert.side_effect = lambda binary_form=False: (
            b"public certificate" if binary_form else {"subject": ((("commonName", "Deco X50(US)"),),)})
        tls.version.return_value = "TLSv1.3"
        tls.cipher.return_value = ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256)
        pending = bytearray()
        requests = []
        user_random = None

        def send(packet):
            nonlocal user_random
            request = json.loads(packet[24:])
            requests.append(request)
            self.assertEqual(request["method"], "login")
            self.assertEqual(packet[:4], b"\x01\x01\x01\0")
            self.assertEqual(packet[8:16], b"\0" * 8)
            self.assertEqual(struct.unpack(">I", packet[20:24])[0],
                             zlib.crc32(packet[:20] + bytes.fromhex("92bf30ed") + packet[24:]))
            if len(requests) == 1:
                self.assertEqual(request["params"]["sub_method"], "pake_register")
                self.assertEqual(request["params"]["username"], hashlib.sha256(self.EMAIL.encode()).hexdigest())
                user_random = base64.b64decode(request["params"]["user_random"])
                response = {"error_code": 0, "result": dict(params)}
                if change_params:
                    change_params(response)
            elif len(requests) == 2:
                if send_error:
                    raise send_error
                self.assertEqual(request["params"]["sub_method"], "pake_share")
                self.assertEqual(set(request["params"]), {"sub_method", "user_share", "user_confirm"})
                share = base64.b64decode(request["params"]["user_share"])
                client = ECC.import_key(share, curve_name="P-256").pointQ
                z = encode((client + -(m * w0)) * y)
                v = encode(generator * (w1 * y % probe.P256_ORDER))
                parts = [hashlib.sha256(b"PAKE V1" + user_random + server_random).digest(),
                         b"", b"", probe.SPAKE_M, probe.SPAKE_N, share, server_share, z, v,
                         w0.to_bytes(32, "big")]
                transcript = b"".join(len(p).to_bytes(8, "little") + p for p in parts)
                keys = HKDF(hashlib.sha256(transcript).digest(), 64, b"", SHA256,
                            context=b"ConfirmationKeys")
                expected = hmac.digest(keys[:32], server_share, "sha256")
                if not hmac.compare_digest(base64.b64decode(request["params"]["user_confirm"]), expected):
                    response = {"error_code": -2203, "message": "PRIVATE_AUTH_FAILURE"}
                else:
                    response = {"error_code": 0, "result": {
                        "sub_method": "pake_share", "stok": "PRIVATE_SESSION_TOKEN",
                        "dev_confirm": base64.b64encode(hmac.digest(keys[32:], share, "sha256")).decode()}}
                if change_reply:
                    change_reply(response)
            else:
                self.fail("Probe must never send a third message or open a TMP channel")
            pending.extend(ParameterProbeTests().frame(response, sequence=packet[16:20]))

        def recv(length):
            size = min(length, 17)  # TLS fragmentation on both messages.
            data = bytes(pending[:size])
            del pending[:size]
            return data

        tls.sendall.side_effect = send
        tls.recv.side_effect = recv
        with patch.object(probe, "make_context", return_value=context), patch.object(
                probe.socket, "create_connection", return_value=raw) as connect:
            result = probe.probe("192.168.68.1", expected_sha256=pin,
                                 owner_email=self.EMAIL, password=password)
        self.assertEqual(connect.call_count, 1)
        tls.__exit__.assert_called_once()
        for secret in (self.EMAIL, self.PASSWORD, password, "PRIVATE", "dev_confirm", "user_share",
                       "user_confirm", hashlib.sha256(self.EMAIL.encode()).hexdigest()):
            self.assertNotIn(secret, json.dumps(result))
        return result, tls, requests

    def test_full_mutual_authentication_closes_after_two_messages(self):
        result, tls, requests = self.run_server()
        self.assertEqual(result["state"], "authentication_verified")
        self.assertEqual(result["authentication"], "verified")
        self.assertTrue(result["password_proof_sent"])
        self.assertTrue(result["server_proof_verified"])
        self.assertTrue(result["account_credentials_verified"])
        self.assertFalse(result["account_role_verified"])
        self.assertTrue(result["session_token_received"])
        self.assertEqual(result["optimization"], "not_tested")
        self.assertEqual(result["application_messages_sent"], 2)
        self.assertEqual(result["application_bytes_sent"], sum(len(c.args[0]) for c in tls.sendall.call_args_list))
        self.assertEqual(len(requests), 2)

    def test_wrong_password_is_rejected_once_without_alternative_encoding(self):
        result, tls, _ = self.run_server(password="wrong password")
        self.assertEqual(result["state"], "authentication_rejected")
        self.assertEqual(result["api_code"], -2203)
        self.assertFalse(result["account_credentials_verified"])
        self.assertFalse(result["server_proof_verified"])
        self.assertEqual(tls.sendall.call_count, 2)

    def test_api_success_without_valid_mutual_proof_never_authenticates(self):
        def corrupt(reply):
            reply["result"]["dev_confirm"] = base64.b64encode(b"\0" * 32).decode()
        result, tls, _ = self.run_server(change_reply=corrupt)
        self.assertEqual(result["state"], "server_proof_mismatch")
        self.assertFalse(result["account_credentials_verified"])
        self.assertFalse(result["session_token_received"])
        self.assertEqual(tls.sendall.call_count, 2)

    def test_missing_token_or_wrong_response_shape_never_completes(self):
        for field, value in [("stok", ""), ("stok", None), ("sub_method", "other"),
                             ("dev_confirm", "bad")]:
            result, _, _ = self.run_server(change_reply=lambda r: r["result"].update({field: value}))
            self.assertEqual(result["state"], "protocol_error")
            self.assertFalse(result["account_credentials_verified"])

    def test_unexpected_password_profile_curve_or_cost_sends_no_proof(self):
        for field, value, reason in [
            ("extra_crypt", {}, "unsupported_password_profile"),
            ("iterations", 1000001, "authentication_iteration_limit"),
            ("dev_salt", base64.b64encode(b"s").decode(), "invalid_parameter_length"),
            ("dev_share", base64.b64encode(b"\x04" + b"\0" * 64).decode(), "invalid_curve_point"),
        ]:
            with self.subTest(field=field):
                result, tls, _ = self.run_server(change_params=lambda r: r["result"].update({field: value}))
                self.assertEqual(result["state"], "protocol_error")
                self.assertEqual(result["protocol_error"], reason)
                self.assertFalse(result["password_proof_attempted"])
                tls.sendall.assert_called_once()

    def test_pin_mismatch_never_sends_credentials_or_registration(self):
        result, tls, _ = self.run_server(pin="0" * 64)
        self.assertEqual(result["state"], "certificate_pin_mismatch")
        tls.sendall.assert_not_called()

    def test_login_rejection_in_first_stage_does_not_send_password_proof(self):
        result, tls, _ = self.run_server(change_params=lambda r: r.update(error_code=-2101))
        self.assertEqual(result["state"], "pake_rejected")
        self.assertFalse(result["password_proof_attempted"])
        tls.sendall.assert_called_once()

    def test_partial_proof_write_is_uncertain_and_never_retried(self):
        result, tls, _ = self.run_server(send_error=TimeoutError())
        self.assertEqual(result["state"], "timeout")
        self.assertTrue(result["password_proof_attempted"])
        self.assertFalse(result["password_proof_sent"])
        self.assertIsNone(result["application_bytes_sent"])
        self.assertFalse(result["account_credentials_verified"])
        self.assertEqual(tls.sendall.call_count, 2)

    def test_unmasked_identity_and_invalid_scalars_are_rejected(self):
        for scalar in (0, probe.P256_ORDER):
            with self.assertRaises(probe.ProtocolError):
                probe._spake2p_proofs(b"context", 1, 1, scalar, probe.SPAKE_N)
        with self.assertRaisesRegex(probe.ProtocolError, "invalid_curve_point"):
            probe._spake2p_proofs(b"context", 1, 1, 1, probe.SPAKE_N)

    def test_credentials_and_crypto_dependency_are_checked_before_connecting(self):
        with patch.object(probe.socket, "create_connection") as connect:
            for password in ("", "x\0y", "x" * 257):
                with self.assertRaises(ValueError):
                    probe.probe("192.168.68.1", expected_sha256=self.PIN,
                                owner_email=self.EMAIL, password=password)
            with self.assertRaises(ValueError):
                probe.probe("192.168.68.1", password=self.PASSWORD)
            with patch.object(probe, "crypto_modules", side_effect=ValueError("missing dependency")):
                with self.assertRaises(ValueError):
                    probe.probe("192.168.68.1", expected_sha256=self.PIN,
                                owner_email=self.EMAIL, password=self.PASSWORD)
            connect.assert_not_called()

    def test_cli_hidden_client_credentials_no_password_argument(self):
        fake = {"state": "authentication_verified"}
        args = ["probe", "192.168.68.1", "--expected-sha256", self.PIN, "--authenticate"]
        with patch.object(probe.sys, "argv", args), patch.object(probe.sys.stdin, "isatty", return_value=True), \
                patch.object(probe.getpass, "getpass", side_effect=[" " + self.EMAIL + " ", self.PASSWORD]) as prompt, \
                patch.object(probe, "probe", return_value=fake) as run, patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(probe.main(), 0)
            self.assertEqual(prompt.call_count, 2)
            run.assert_called_once_with("192.168.68.1", 8, self.PIN,
                                        owner_email=self.EMAIL, password=self.PASSWORD)
            self.assertNotIn(self.PASSWORD, out.getvalue())

    def test_cli_modes_exclusive_pin_required_and_interactive_only(self):
        for flags in [("--authenticate",), ("--authenticate", "--pake-parameters"),
                      ("--authenticate", "--expected-sha256", self.PIN)]:
            with patch.object(probe.sys, "argv", ["probe", "192.168.68.1", *flags]), \
                    patch.object(probe.sys.stdin, "isatty", return_value=False), \
                    patch.object(probe, "probe") as run, patch("sys.stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit):
                    probe.main()
                run.assert_not_called()

    def test_cli_never_falls_back_to_visible_password_input(self):
        args = ["probe", "192.168.68.1", "--expected-sha256", self.PIN, "--authenticate"]
        with patch.object(probe.sys, "argv", args), patch.object(probe.sys.stdin, "isatty", return_value=True), \
                patch.object(probe.getpass, "getpass", side_effect=probe.getpass.GetPassWarning()), \
                patch.object(probe, "probe") as run, patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit):
                probe.main()
            run.assert_not_called()
