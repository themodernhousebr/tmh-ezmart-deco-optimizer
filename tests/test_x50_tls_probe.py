"""Validate trust policy and output without connecting to a Deco."""
import base64
import hashlib
import hmac
import importlib.util
import io
import json
import os
from pathlib import Path
import ssl
import struct
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
import zlib


def hkdf32(key, salt, info):
    # Independent one-block RFC 5869 expansion, not the production HKDF helper.
    prk = hmac.digest(salt or b"\0" * 32, key, "sha256")
    return hmac.digest(prk, info + b"\x01", "sha256")


spec = importlib.util.spec_from_file_location(
    "x50_tls_probe", Path(__file__).resolve().parents[1] / "ezmart_deco_optimizer/x50_tpap.py")
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
        share, own, peer, shared = probe._spake2p_proofs(context, w0, w1, scalar, server, b"client", b"server")
        self.assertEqual(share.hex(),
                         "04ef3bd051bf78a2234ec0df197f7828060fe9856503579bb1733009042c15c0c1d"
                         "e127727f418b5966afadfdd95a6e4591d171056b333dab97a79c7193e341727")
        self.assertEqual(own.hex(), "926cc713504b9b4d76c9162ded04b5493e89109f6d89462cd33adc46fda27527")
        self.assertEqual(peer.hex(), "9747bcc4f8fe9f63defee53ac9b07876d907d55047e6ff2def2e7529089d3e68")
        self.assertEqual(shared.hex(), "0c5f8ccd1413423a54f6c1fb26ff01534a87f893779c6e68666d772bfd91f3e7")

    def run_server(self, password=PASSWORD, change_params=None, change_reply=None,
                   pin=PIN, send_error=None, read_info=False, channel_peer=None,
                   scan_only=False, scan_timeout=120, optimize=False, checkpoint=None, report=None,
                   before_connect=None, login_verified=None, guest_callback=None, guest_read=False):
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
            if packet[2] == 2 and channel_peer is not None:
                pending.extend(channel_peer.accept(packet))
                return
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
                if channel_peer is not None:
                    channel_peer.bind(hkdf32(hashlib.sha256(transcript).digest(), b"", b"SharedKey"))
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
                                 owner_email=self.EMAIL, password=password, read_info=read_info,
                                 scan_only=scan_only, scan_timeout=scan_timeout, optimize=optimize,
                                 _checkpoint=checkpoint, _report=report,
                                 _before_connect=before_connect, _login_verified=login_verified,
                                 _guest_callback=guest_callback, _guest_read=guest_read)
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

    def test_login_guard_hooks_only_release_after_verified_password_proof(self):
        before, verified = MagicMock(), MagicMock()
        self.run_server(before_connect=before, login_verified=verified)
        before.assert_called_once()
        verified.assert_called_once()
        verified.reset_mock()
        self.run_server(password='wrong password', login_verified=verified)
        verified.assert_not_called()
        with patch.object(probe.socket, 'create_connection') as connect:
            def block():
                raise probe.ProtocolError('authentication_cooldown')
            result = probe.probe('192.168.68.1', _before_connect=block)
            self.assertEqual(result['protocol_error'], 'authentication_cooldown')
            connect.assert_not_called()

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


class ReadOnlyPeer:
    """Independent framed TMP server over CCM; all traffic stays in memory."""

    def __init__(self, mutate_wire=None, mutate_tmp=None, api_error=False,
                 responses=None, on_request=None, allow_apply=False, allow_guest=False,
                 whole_crc=False, fragment_size=47):
        self.mutate_wire, self.mutate_tmp = mutate_wire, mutate_tmp
        self.whole_crc, self.fragment_size = whole_crc, fragment_size
        self.api_error = api_error
        self.responses = responses or {}
        self.on_request = on_request
        self.allow_guest = allow_guest
        self.allow_apply = allow_apply
        self.sequence = 0x12345678
        self.client_sequences = []
        self.requests = []
        self.pulls = []
        self.remainder = None
        self.last_frame = None
        self.keepalives = 0

    def bind(self, shared):
        self.key = hkdf32(shared, b"tp-kdf-salt-aes128-key", b"tp-kdf-info-aes128-key")[:16]
        self.iv = hkdf32(shared, b"tp-kdf-salt-aes128-iv", b"tp-kdf-info-aes128-iv")[:8]

    def frame(self, data):
        from Crypto.Cipher import AES
        seq = self.sequence.to_bytes(4, "little")
        self.sequence += 1
        cipher = AES.new(self.key, AES.MODE_CCM, nonce=self.iv + seq, mac_len=16)
        ciphertext, tag = cipher.encrypt_and_digest(data)
        body = ciphertext + tag
        head = b"\x01\x01\x02\0" + len(body).to_bytes(4, "big") + b"TMP\0\0\0\0\0" + seq
        frame = head + zlib.crc32(head + bytes.fromhex("92bf30ed") + body).to_bytes(4, "big") + body
        if self.mutate_wire:
            frame = self.mutate_wire(frame)
        return frame

    @staticmethod
    def tmp_response(opcode, tx, chunk, total=None, offset=0):
        total = len(chunk) if total is None else total
        business = struct.pack(">BBHBBHIII", 1, 2, opcode, 5, 0, tx,
                               zlib.crc32(chunk), total, offset) + chunk
        header = b"\x01\x01\x05\0" + struct.pack(">HHI", len(business), 0, 777)
        crc = zlib.crc32(header + bytes.fromhex("5a6b7c8d") + business)
        return header + crc.to_bytes(4, "big") + business

    def accept(self, packet):
        from Crypto.Cipher import AES
        assert packet[:4] == b"\x01\x01\x02\0"
        assert packet[8:16] == b"TMP\0\0\0\0\0"
        assert int.from_bytes(packet[4:8], "big") == len(packet) - 24
        assert int.from_bytes(packet[20:24], "big") == zlib.crc32(
            packet[:20] + bytes.fromhex("92bf30ed") + packet[24:])
        seq = packet[16:20]
        assert seq not in self.client_sequences
        if self.client_sequences:
            assert int.from_bytes(seq, "little") == int.from_bytes(self.client_sequences[-1], "little") + 1
        self.client_sequences.append(seq)
        cipher = AES.new(self.key, AES.MODE_CCM, nonce=self.iv + seq, mac_len=16)
        data = cipher.decrypt_and_verify(packet[24:-16], packet[-16:])
        if data == b"\x01\x01\x04\0":
            self.keepalives += 1
            return b""
        assert data[:4] == b"\x01\x01\x05\0"
        assert int.from_bytes(data[4:6], "big") == len(data) - 16
        assert int.from_bytes(data[12:16], "big") == zlib.crc32(
            data[:12] + bytes.fromhex("5a6b7c8d") + data[16:])
        v, sub, op, kind, err, tx, crc, total, offset = struct.unpack_from(">BBHBBHIII", data, 16)
        assert (v, sub, err) == (1, 2, 0)
        body = data[36:]
        assert crc == zlib.crc32(body)
        if kind == 4:
            previous_op, previous_tx, full = self.remainder
            assert (op, tx) == (previous_op, previous_tx)
            assert body == b"" and total == 0 and 0 < offset < len(full)
            self.pulls.append(op)
            response = self.tmp_response(op, tx, full[offset:] if self.fragment_size == 47 else full[offset:offset+self.fragment_size], len(full), offset)
        else:
            assert kind == 2 and offset == 0 and total == len(body)
            self.requests.append(op)
            # Deliberately do not use the production allowlist to validate writes.
            assert (op in (1, 0x40A0, 0x4009, 0x400F) or (op == 0x424F and op in self.responses)
                    or (op == 0x4250 and self.allow_apply) or (op == 0x4008 and self.allow_guest))
            if self.on_request:
                self.on_request(op)
            if op == 1:
                assert body == b""
                response = self.tmp_response(op, tx, b"a" * 32)
            else:
                request = json.loads(body)
                assert set(request) == {"config_version", "params"}
                if op == 0x4008:
                    assert self.allow_guest
                    params = request['params']
                    assert set(params) == {'band2_4', 'band5_1'}
                    for band in params:
                        assert set(params[band]) == {'guest'}
                        assert set(params[band]['guest']) == {'enable','host_isolation'}
                        assert all(type(v) is bool for v in params[band]['guest'].values())
                        self.responses[0x4009]['result'][band]['guest']['enable'] = params[band]['guest']['enable']
                else:
                    assert request['params'] == {}
                content = {
                    0x40A0: {"mode": "Router", "owner": "PRIVATE"},
                    0x4009: {
                        "band2_4": {"host": {"channel": "9", "password": "PRIVATE", "ssid": "PRIVATE"},
                                    "radio": {"channel": "9", "htmode": "HE40", "bssid": "PRIVATE"}},
                        "band5_1": {"radio": {"channel": "36", "htmode": "HE80"},
                                    "backhaul": {"channel": 36}},
                    },
                    0x400F: {"device_list": [{"device_model": "X50", "hardware_ver": "1.0",
                         "software_ver": "1.8.4 Build 26051413 Rel. 27460", "role": "master",
                         "device_id": "PRIVATE", "mac": "PRIVATE", "nickname": "PRIVATE"}]},
                }.get(op)
                message = {"error_code": 1, "message": "PRIVATE"} if self.api_error and op == 0x4009 else {
                    "error_code": 0, "result": content}
                message = self.responses.get(op, message)
                full = message if isinstance(message, bytes) else json.dumps(message).encode()
                self.remainder = (op, tx, full)
                response = self.tmp_response(op, tx, full[:self.fragment_size], len(full))
        if self.whole_crc and op != 1:
            payload = bytearray(response[16:])
            payload[8:12] = zlib.crc32(full).to_bytes(4, 'big')
            outer_crc = zlib.crc32(response[:12] + bytes.fromhex('5a6b7c8d') + payload)
            response = response[:12] + outer_crc.to_bytes(4, 'big') + payload
        if self.mutate_tmp:
            response = self.mutate_tmp(response)
        # Exercise both stream splitting and coalescing across TPAP frames,
        # plus independent TMP business fragments requested with a pull.
        output = self.frame(response[:7])
        output += self.frame(response[7:] + b"\x01\x01\x04\0\x01\x01\x02\0")
        self.last_frame = output
        return output


class ReadOnlyProbeTests(unittest.TestCase):
    def run_peer(self, peer=None, **kwargs):
        peer = peer or ReadOnlyPeer()
        result, tls, requests = AuthenticationProbeTests().run_server(
            read_info=True, channel_peer=peer, **kwargs)
        return result, tls, peer

    def test_authenticated_encrypted_tmp_reads_without_scan_or_apply(self):
        result, tls, peer = self.run_peer()
        self.assertEqual(result["state"], "readonly_verified")
        self.assertTrue(result["tmp_session_verified"])
        self.assertEqual(result["optimization"], "not_tested")
        self.assertFalse(result["account_role_verified"])
        self.assertEqual(peer.requests, [1, 0x40A0, 0x4009, 0x400F])
        self.assertEqual(peer.pulls, [0x40A0, 0x4009, 0x400F])
        self.assertGreater(peer.keepalives, 0)
        self.assertGreater(result["channel_frames_received"], 4)
        self.assertEqual(result["reads"]["mode"]["data"], {"mode": "Router"})
        self.assertEqual(result["reads"]["channels"]["data"]["band2_4"]["radio"],
                         {"channel": 9, "htmode": "HE40"})
        self.assertEqual(result["reads"]["devices"]["data"]["nodes"][0]["model"], "X50")
        self.assertEqual(result["application_messages_sent"], tls.sendall.call_count)
        self.assertEqual(result["application_bytes_sent"], sum(len(c.args[0]) for c in tls.sendall.call_args_list))

    def test_read_mode_requires_credentials_and_verified_mutual_proof(self):
        with patch.object(probe.socket, "create_connection") as connect:
            with self.assertRaises(ValueError):
                probe.probe("192.168.68.1", read_info=True)
            connect.assert_not_called()
        for kwargs in ({"password": "wrong"}, {"pin": "0" * 64}, {"change_reply": lambda r:
                r["result"].update(dev_confirm=base64.b64encode(b"0" * 32).decode())}):
            _, tls, peer = self.run_peer(**kwargs)
            self.assertEqual(peer.requests, [])
            self.assertLessEqual(tls.sendall.call_count, 2)

    def test_channel_tag_corruption_is_rejected_even_with_valid_crc(self):
        def corrupt(frame):
            body = frame[24:-1] + bytes([frame[-1] ^ 1])
            return frame[:20] + zlib.crc32(frame[:20] + bytes.fromhex("92bf30ed") + body).to_bytes(4, "big") + body
        result, tls, peer = self.run_peer(ReadOnlyPeer(mutate_wire=corrupt))
        self.assertEqual(result["protocol_error"], "channel_tag_mismatch")
        self.assertEqual(result["stage"], "tmp_allocate")
        self.assertEqual(peer.requests, [1])
        self.assertEqual(tls.sendall.call_count, 3)

    def test_wrong_channel_header_crc_and_size_stop_without_retry(self):
        for mutate, code in (
            (lambda f: f[:1] + b"\x02" + f[2:], "unexpected_channel_header"),
            (lambda f: f[:8] + b"OTHER\0\0\0" + f[16:], "unexpected_channel_header"),
            (lambda f: f[:20] + b"\0" * 4 + f[24:], "channel_checksum_mismatch"),
            (lambda f: f[:4] + b"\xff" * 4 + f[8:], "invalid_channel_length"),
        ):
            with self.subTest(code=code):
                result, tls, peer = self.run_peer(ReadOnlyPeer(mutate_wire=mutate))
                self.assertEqual(result["protocol_error"], code)
                self.assertEqual(tls.sendall.call_count, 3)

    def test_replayed_channel_frame_is_rejected(self):
        previous = []
        def repeat(frame):
            if not previous:
                previous.append(frame)
            return previous[0]
        result, _, _ = self.run_peer(ReadOnlyPeer(mutate_wire=repeat))
        self.assertEqual(result["protocol_error"], "repeated_channel_sequence")

    def test_tmp_correlation_checks_survive_valid_encryption(self):
        def changed(field, value):
            def mutate(data):
                payload = bytearray(data[16:])
                start, size = field
                payload[start:start+size] = value.to_bytes(size, "big")
                crc = zlib.crc32(data[:12] + bytes.fromhex("5a6b7c8d") + payload)
                return data[:12] + crc.to_bytes(4, "big") + payload
            return mutate
        for field, value, code in (
            ((6, 2), 9, "tmp_response_mismatch"),
            ((2, 2), 0x4250, "tmp_response_mismatch"),
            ((12, 4), 262145, "tmp_fragment_invalid"),
            ((16, 4), 1, "tmp_fragment_invalid"),
            ((8, 4), 0, "tmp_business_invalid"),
            ((5, 1), 1, "tmp_request_rejected"),
        ):
            with self.subTest(code=code):
                result, _, peer = self.run_peer(ReadOnlyPeer(mutate_tmp=changed(field, value)))
                self.assertEqual(result["protocol_error"], code)
                if code == 'tmp_business_invalid':
                    detail = result['tmp_business_diagnostic']
                    self.assertTrue(detail['version_matches'])
                    self.assertFalse(detail['chunk_crc_matches'])
                    self.assertEqual(detail['opcode'], 1)
                    self.assertEqual(detail['offset'], 0)
                    self.assertEqual(detail['chunk_bytes'], detail['total_bytes'])
                self.assertEqual(peer.requests, [1])

    def test_large_inventory_with_complete_message_crc_and_real_fragment_size(self):
        devices = {'error_code': 0, 'result': {'device_list': [
            {'device_model': 'X50', 'hardware_ver': '1.30',
             'software_ver': '1.8.4 Build 26051413 Rel. 27460', 'role': 'master',
             'private_padding': 'PRIVATE'*2000}]}}
        peer = ReadOnlyPeer(whole_crc=True, fragment_size=8156, responses={0x400F: devices})
        result, _, peer = self.run_peer(peer)
        self.assertEqual(result['state'], 'readonly_verified')
        self.assertEqual(peer.requests, [1, 0x40A0, 0x4009, 0x400F])
        self.assertIn(0x400F, peer.pulls)
        self.assertEqual(result['reads']['devices']['data']['nodes'][0]['hardware'], '1.30')
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_complete_message_crc_must_stay_constant_across_fragments(self):
        def change_second_fragment(frame):
            payload = bytearray(frame[16:])
            if int.from_bytes(payload[16:20], 'big') > 0:
                payload[8:12] = (int.from_bytes(payload[8:12], 'big') ^ 1).to_bytes(4, 'big')
                outer = zlib.crc32(frame[:12] + bytes.fromhex('5a6b7c8d') + payload)
                return frame[:12] + outer.to_bytes(4, 'big') + payload
            return frame
        result, _, _ = self.run_peer(ReadOnlyPeer(whole_crc=True, mutate_tmp=change_second_fragment))
        self.assertEqual(result['protocol_error'], 'tmp_business_invalid')
        self.assertNotIn('mode', result['reads'])

    def test_full_message_crc_corruption_never_exports_inventory(self):
        def corrupt_inventory_crc(frame):
            payload = bytearray(frame[16:])
            if int.from_bytes(payload[2:4], 'big') == 0x400F:
                crc = int.from_bytes(payload[8:12], 'big') ^ 1
                payload[8:12] = crc.to_bytes(4, 'big')
                outer = zlib.crc32(frame[:12] + bytes.fromhex('5a6b7c8d') + payload)
                return frame[:12] + outer.to_bytes(4, 'big') + payload
            return frame
        result, _, peer = self.run_peer(ReadOnlyPeer(whole_crc=True, mutate_tmp=corrupt_inventory_crc))
        self.assertEqual(result['protocol_error'], 'tmp_message_checksum_mismatch')
        self.assertNotIn('devices', result['reads'])
        self.assertEqual(peer.requests.count(0x400F), 1)

    def test_api_failure_does_not_claim_all_reads_verified(self):
        result, _, peer = self.run_peer(ReadOnlyPeer(api_error=True))
        self.assertEqual(result["state"], "readonly_incomplete")
        self.assertEqual(result["reads"]["channels"], {"state": "api_error", "api_code": 1})
        self.assertEqual(peer.requests, [1, 0x40A0, 0x4009, 0x400F])

    def test_arbitrary_writes_and_acs_reads_are_blocked_before_send(self):
        channel = MagicMock()
        for opcode in (0x424F, 0x4250, 0x4263, 0x4016, 0x4008, 0x40A1):
            with self.assertRaisesRegex(probe.ProtocolError, "tmp_opcode_not_allowed"):
                probe.TMPReader(channel).request(opcode, 20)
        channel.send.assert_not_called()

    def test_read_projectors_reject_unknown_and_malformed_values(self):
        for name, result in (("mode", {"mode": "PRIVATE"}), ("devices", {"device_list": []}),
                             ("channels", {"band2_4": {"radio": {"channel": True, "htmode": "PRIVATE"}}})):
            out = probe.project_read(name, json.dumps({"error_code": 0, "result": result}).encode())
            self.assertEqual(out["state"], "unrecognized_response")
            self.assertNotIn("PRIVATE", str(out))
        for body in (b'[]', b'{"error_code":true}', b'{"error_code":0,"error_code":1}', b'not json'):
            with self.assertRaises(probe.ProtocolError):
                probe.project_read("mode", body)

    def test_channel_deadline_and_send_limits(self):
        report = {"application_bytes_sent": 0, "application_messages_sent": 0,
                  "channel_frames_received": 0}
        tls = MagicMock()
        channel = probe.TMPChannel(tls, b"s" * 32, report)
        channel.deadline = time.monotonic() - 1
        with self.assertRaises(TimeoutError):
            channel.exact(4)
        with self.assertRaises(TimeoutError):
            channel.send(b"test")
        tls.sendall.assert_not_called()
        channel.sent_frames = 1024
        with self.assertRaisesRegex(probe.ProtocolError, "channel_send_limit"):
            channel.send(b"test")

    def test_cli_read_info_uses_hidden_client_credentials(self):
        args = ["probe", "192.168.68.1", "--expected-sha256", AuthenticationProbeTests.PIN, "--read-info"]
        with patch.object(probe.sys, "argv", args), patch.object(probe.sys.stdin, "isatty", return_value=True), \
                patch.object(probe.getpass, "getpass", side_effect=["owner@example.test", "secret"]) as prompt, \
                patch.object(probe, "probe", return_value={"state": "readonly_verified"}) as run, \
                patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(probe.main(), 0)
            self.assertEqual(prompt.call_count, 2)
            self.assertTrue(run.call_args.kwargs["read_info"])


class ScanProbeTests(unittest.TestCase):
    def peer(self, needed=True, **kwargs):
        # Shape reported by the physical X50 on 15/11/2026. No host/radio
        # channel is present; its backhaul numbers must stay in that scope.
        responses = {
            0x400F: {"error_code": 0, "result": {"device_list": [
                {"device_model": "X50", "hardware_ver": "1.30", "role": role,
                 "software_ver": "1.8.4 Build 26051413 Rel. 27460"}
                for role in ("master", "slave")]}},
            0x4009: {"error_code": 0, "result": {
                "band2_4": {"radio": {"htmode": "HT40"}, "backhaul": {"channel": 10}},
                "band5_1": {"radio": {"htmode": "HT80"}, "backhaul": {"channel": 36}},
            }},
            0x424F: {"error_code": 0, "result": {
                "need_optimize": needed, "optimize_item": {"channel": needed, "PRIVATE": "PRIVATE"},
                "optimize_time": 20, "PRIVATE": "PRIVATE"}},
        }
        return ReadOnlyPeer(responses=responses, **kwargs)

    def run_peer(self, peer=None, **kwargs):
        peer = peer or self.peer()
        result, tls, _ = AuthenticationProbeTests().run_server(
            scan_only=True, channel_peer=peer, **kwargs)
        return result, tls, peer

    def test_one_scan_after_preflight_then_channel_observation_never_apply(self):
        for needed in (True, False):
            with self.subTest(needed=needed):
                result, _, peer = self.run_peer(self.peer(needed))
                self.assertEqual(peer.requests, [1, 0x40A0, 0x4009, 0x400F, 0x424F, 0x4009])
                self.assertEqual(peer.pulls.count(0x424F), 1)
                self.assertEqual(result["state"], "scan_response_received")
                self.assertEqual(result["stage"], "complete")
                self.assertEqual(result["optimization"], "not_tested")
                self.assertFalse(result["apply_command_sent"])
                scan = result["scan"]
                self.assertTrue(scan["request_attempted"])
                self.assertTrue(scan["request_sent"])
                self.assertTrue(scan["response_received"])
                self.assertEqual(scan["need_optimize"], needed)
                self.assertEqual(scan["estimated_apply_seconds"], 20)
                self.assertFalse(scan["fresh_scan_confirmed"])
                self.assertTrue(scan["result_may_be_cached"])
                self.assertEqual(result["channel_comparison"], {
                    "state": "complete", "compared_fields": [
                        {"band": "band2_4", "section": "backhaul"},
                        {"band": "band5_1", "section": "backhaul"}],
                    "channel_changes": [], "observed_channels_changed": False})

    def test_unchanged_physical_read_fixture_still_never_sends_scan(self):
        result, _, peer = ReadOnlyProbeTests().run_peer(self.peer())
        self.assertEqual(result["state"], "readonly_verified")
        self.assertEqual(peer.requests, [1, 0x40A0, 0x4009, 0x400F])
        self.assertEqual(result["reads"]["devices"]["data"]["nodes"][0]["hardware"], "1.30")
        self.assertNotIn("scan", result)

    def test_unauthenticated_or_incomplete_preflight_never_scans(self):
        for kwargs in ({"password": "wrong"}, {"pin": "0" * 64}):
            result, _, peer = self.run_peer(**kwargs)
            self.assertFalse(result["scan"]["request_attempted"])
            self.assertEqual(peer.requests, [])
        for op in (0x40A0, 0x4009, 0x400F):
            peer = self.peer()
            peer.responses[op] = {"error_code": 1}
            result, _, peer = self.run_peer(peer)
            self.assertEqual(result["state"], "scan_preflight_failed")
            self.assertFalse(result["scan"]["request_attempted"])
            self.assertNotIn(0x424F, peer.requests)

    def test_other_primary_profile_is_read_but_not_scanned(self):
        for key, value in (("device_model", "M9Plus"), ("hardware_ver", "1.0"),
                           ("software_ver", "1.8.0"), ("role", "slave")):
            peer = self.peer()
            peer.responses[0x400F]["result"]["device_list"][0][key] = value
            result, _, peer = self.run_peer(peer)
            self.assertEqual(result["state"], "scan_target_unsupported")
            self.assertNotIn(0x424F, peer.requests)
        peer = self.peer()
        peer.responses[0x40A0] = {"error_code": 0, "result": {"mode": "AP"}}
        self.assertEqual(self.run_peer(peer)[0]["state"], "scan_target_unsupported")

    def test_rejection_or_inconsistent_recommendation_does_not_retry_or_apply(self):
        for response, state in (
            ({"error_code": -4104, "message": "PRIVATE"}, "scan_rejected"),
            ({"error_code": 0, "result": None}, "scan_response_unrecognized"),
            ({"error_code": 0, "result": {"need_optimize": True,
                "optimize_item": {"channel": False}}}, "scan_response_unrecognized"),
            ({"error_code": 0, "result": {"need_optimize": 1,
                "optimize_item": {"channel": True}}}, "scan_response_unrecognized"),
        ):
            peer = self.peer()
            peer.responses[0x424F] = response
            result, _, peer = self.run_peer(peer)
            self.assertEqual(result["state"], state)
            self.assertNotIn("need_optimize", result["scan"])
            self.assertEqual(peer.requests.count(0x424F), 1)
            self.assertFalse(result["apply_command_sent"])

    def test_invalid_scan_json_fails_closed_with_redacted_report(self):
        for body in (b'PRIVATE', b'{"error_code":true}',
                     b'{"error_code":0,"error_code":1}', b'{"error_code":999999999999}'):
            peer = self.peer()
            peer.responses[0x424F] = body
            result, _, peer = self.run_peer(peer)
            self.assertEqual(result["state"], "protocol_error")
            self.assertEqual(result["scan"]["state"], "scan_response_unrecognized")
            self.assertEqual(peer.requests.count(0x424F), 1)
            self.assertEqual(peer.requests.count(0x4009), 1)

    def test_partial_scan_send_is_uncertain_without_reconnect_or_retry(self):
        def fail(op):
            if op == 0x424F:
                raise TimeoutError()
        result, _, peer = self.run_peer(self.peer(on_request=fail))
        self.assertEqual(result["state"], "timeout")
        self.assertEqual(result["stage"], "scan")
        self.assertEqual(result["scan"]["state"], "response_unknown")
        self.assertTrue(result["scan"]["request_attempted"])
        self.assertFalse(result["scan"]["request_sent"])
        self.assertFalse(result["scan"]["response_received"])
        self.assertIsNone(result["application_bytes_sent"])
        self.assertEqual(peer.requests.count(0x424F), 1)

    def test_reply_timeout_uses_scan_deadline_and_does_not_resend(self):
        original = probe.TMPReader.request
        with patch.object(probe.TMPReader, "request", autospec=True) as request:
            # Exercise the real request implementation except the scan receive.
            def operation(reader, opcode, timeout):
                if opcode != 0x424F:
                    return original(reader, opcode, timeout)
                self.assertEqual(timeout, 150)
                with patch.object(reader.channel, "exact", side_effect=TimeoutError()):
                    return original(reader, opcode, timeout)
            request.side_effect = operation
            result, _, peer = self.run_peer(scan_timeout=150)
        self.assertEqual(result["state"], "timeout")
        self.assertTrue(result["scan"]["request_sent"])
        self.assertFalse(result["scan"]["response_received"])
        self.assertEqual(peer.requests.count(0x424F), 1)

    def test_second_scan_blocked_even_after_uncertain_first_send(self):
        channel = MagicMock()
        channel.report = {"scan": {}}
        channel.send.side_effect = TimeoutError()
        reader = probe.TMPReader(channel, allow_scan=True)
        with self.assertRaises(TimeoutError):
            reader.request(0x424F, 120)
        with self.assertRaisesRegex(probe.ProtocolError, "scan_already_attempted"):
            reader.request(0x424F, 120)
        for opcode in (0x4250, 0x4008, 0x40A1, 0x4016):
            with self.assertRaisesRegex(probe.ProtocolError, "tmp_opcode_not_allowed"):
                reader.request(opcode, 20)
        channel.send.assert_called_once()

    def test_post_scan_failure_preserves_received_recommendation(self):
        peer = self.peer()
        def fail(op):
            if op == 0x4009 and 0x424F in peer.requests:
                raise ConnectionRefusedError()
        peer.on_request = fail
        result, _, _ = self.run_peer(peer)
        self.assertEqual(result["state"], "connection_refused")
        self.assertEqual(result["stage"], "read_channels_after_scan")
        self.assertEqual(result["scan"]["state"], "scan_response_received")
        self.assertNotIn("channel_comparison", result)
        self.assertEqual(peer.requests.count(0x424F), 1)

    def test_post_scan_api_error_does_not_claim_complete_observation(self):
        peer = self.peer()
        def reject(op):
            if op == 0x4009 and 0x424F in peer.requests:
                peer.responses[op] = {"error_code": 1}
        peer.on_request = reject
        result, _, _ = self.run_peer(peer)
        self.assertEqual(result["state"], "scan_observation_incomplete")
        self.assertEqual(result["scan"]["state"], "scan_response_received")
        self.assertEqual(result["channel_comparison"]["state"], "unavailable")
        self.assertIsNone(result["channel_comparison"]["observed_channels_changed"])

    def test_channel_change_is_scoped_and_never_claims_application(self):
        peer = self.peer()
        def change(op):
            if op == 0x4009 and 0x424F in peer.requests:
                peer.responses[op]["result"]["band2_4"]["backhaul"]["channel"] = 6
        peer.on_request = change
        result, _, _ = self.run_peer(peer)
        self.assertEqual(result["channel_comparison"]["channel_changes"], [
            {"band": "band2_4", "section": "backhaul", "before": 10, "after": 6}])
        self.assertTrue(result["channel_comparison"]["observed_channels_changed"])
        self.assertFalse(result["apply_command_sent"])
        self.assertEqual(result["optimization"], "not_tested")

    def test_comparison_never_conflates_scopes_or_missing_observations(self):
        before = {"state": "available", "data": {"band2_4": {"backhaul": {"channel": 10}}}}
        for after in ({"state": "api_error"}, {"state": "available", "data": {
                "band2_4": {"radio": {"channel": 6}}}}):
            out = probe.compare_channels(before, after)
            self.assertEqual(out["state"], "unavailable")
            self.assertIsNone(out["observed_channels_changed"])
        after = {"state": "available", "data": {"band2_4": {
            "backhaul": {"channel": 10}, "host": {"channel": 6}}}}
        self.assertEqual(probe.compare_channels(before, after)["state"], "partial")

    def test_scan_credentials_timeout_and_mode_checked_before_connecting(self):
        for args in ({"scan_only": True}, {"scan_timeout": 29}, {"scan_timeout": 181},
                     {"scan_timeout": True}, {"read_info": True, "scan_only": True}):
            with patch.object(probe.socket, "create_connection") as connect:
                with self.assertRaises(ValueError):
                    probe.probe("192.168.68.1", **args)
                connect.assert_not_called()

    def test_cli_scan_uses_hidden_credentials_and_only_explicit_mode(self):
        args = ["probe", "192.168.68.1", "--expected-sha256", AuthenticationProbeTests.PIN,
                "--scan-only", "--scan-timeout", "150"]
        with patch.object(probe.sys, "argv", args), patch.object(probe.sys.stdin, "isatty", return_value=True), \
                patch.object(probe.getpass, "getpass", side_effect=["owner@example.test", "secret"]) as prompt, \
                patch.object(probe, "probe", return_value={"state": "scan_response_received"}) as run, \
                patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(probe.main(), 0)
            self.assertEqual(prompt.call_count, 2)
            self.assertTrue(run.call_args.kwargs["scan_only"])
            self.assertEqual(run.call_args.kwargs["scan_timeout"], 150)
        for flags in (("--scan-only", "--read-info"), ("--scan-only", "--authenticate"), ("--scan-only",)):
            with patch.object(probe.sys, "argv", ["probe", "192.168.68.1", *flags]), \
                    patch.object(probe, "probe") as run, patch("sys.stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit):
                    probe.main()
                run.assert_not_called()


class ApplyProbeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ezmart-x50-test-", dir="/tmp")
        self.addCleanup(self.directory.cleanup)
        self.journal = Path(self.directory.name) / "actions.json"
        self.args = dict(host="192.168.68.1", expected_sha256=AuthenticationProbeTests.PIN,
                         owner_email=AuthenticationProbeTests.EMAIL, password=AuthenticationProbeTests.PASSWORD,
                         request_id="x50-opt-001", journal=str(self.journal))
        self.sessions = []
        self.clock = 1800000000.0

    def advance(self, seconds):
        self.clock += seconds

    def run_action(self, needed=True, ack=None, fail_apply=False, observed_change=True,
                   observation_states=None, corrupt_scan=False, **options):
        actual_probe = probe.probe
        observations = iter(observation_states or [])

        def connection(**kwargs):
            applying = kwargs.get("optimize", False)
            if not applying:
                self.assertTrue(kwargs.get("read_info"))
                self.assertNotIn("scan_only", kwargs)
                self.assertNotIn("optimize", kwargs)
                state = next(observations, None)
                if state:
                    self.sessions.append([])
                    return {"state": state}
            peer = ScanProbeTests().peer(needed)
            peer.allow_apply = applying
            peer.responses[0x4250] = {"error_code": 0, "result": None} if ack is None else ack
            if corrupt_scan:
                peer.responses[0x424F] = {"error_code": 0, "result": {"need_optimize": 1}}
            if not applying and observed_change:
                peer.responses[0x4009]["result"]["band2_4"]["backhaul"]["channel"] = 6
                peer.responses[0x4009]["result"]["band2_4"]["radio"]["htmode"] = "HT20"

            def on_request(op):
                if op == 0x4250:
                    # Inspect the actual durable file before allowing simulated I/O.
                    saved = json.loads(self.journal.read_text())["actions"][options.get("request_id", self.args["request_id"])]["report"]
                    self.assertTrue(saved["apply_command_attempted"])
                    self.assertFalse(saved["apply_command_sent"])
                    self.assertTrue(saved["scan"]["need_optimize"])
                    self.assertEqual(saved["channels_before_apply"]["state"], "available")
                    if fail_apply:
                        raise TimeoutError()
            peer.on_request = on_request
            self.sessions.append(peer.requests)
            with patch.object(probe, "probe", actual_probe):
                return AuthenticationProbeTests().run_server(
                    channel_peer=peer, read_info=not applying, optimize=applying,
                    checkpoint=kwargs.get("_checkpoint"), report=kwargs.get("_report"))[0]

        with patch.object(probe, "probe", side_effect=connection), \
                patch.object(probe.time, "sleep", side_effect=self.advance), \
                patch.object(probe.time, "monotonic", side_effect=lambda: self.clock), \
                patch.object(probe.time, "time", side_effect=lambda: self.clock):
            result = probe.run_action(**dict(self.args, **options))
        for secret in (AuthenticationProbeTests.EMAIL, AuthenticationProbeTests.PASSWORD, "PRIVATE"):
            self.assertNotIn(secret, json.dumps(result))
            if self.journal.exists():
                self.assertNotIn(secret, self.journal.read_text())
        return result

    def test_apply_once_with_durable_reservation_then_three_readonly_sessions(self):
        result = self.run_action()
        self.assertEqual(result["state"], "apply_acknowledged")
        self.assertEqual(result["conclusion"], "acknowledged_with_channel_change")
        self.assertEqual(result["apply_ack"], {"state": "accepted", "api_code": 0})
        self.assertTrue(result["apply_command_sent"])
        self.assertEqual(self.sessions[0], [1, 0x40A0, 0x4009, 0x400F, 0x424F, 0x4009, 0x4250])
        self.assertEqual(self.sessions[1:], [[1, 0x40A0, 0x4009, 0x400F]] * 3)
        verification = result["verification"]
        self.assertEqual(verification["stable_samples"], 3)
        self.assertEqual(verification["channel_comparison"]["channel_changes"], [
            {"band": "band2_4", "section": "backhaul", "before": 10, "after": 6}])
        self.assertIn({"band": "band2_4", "section": "radio", "parameter": "htmode",
                       "before": "HT40", "after": "HT20"}, verification["radio_changes"])
        self.assertEqual(os.stat(self.journal).st_mode & 0o777, 0o600)
        saved = json.loads(self.journal.read_text())["actions"]["x50-opt-001"]["report"]
        self.assertEqual(saved, result)
        self.assertGreaterEqual(result["elapsed_seconds"], 50)
        self.assertLess(result["request_session_seconds"], result["elapsed_seconds"])

    def test_negative_or_malformed_recommendation_never_applies(self):
        for options, state in (({"needed": False}, "optimization_not_needed"),
                               ({"corrupt_scan": True}, "scan_response_unrecognized")):
            with self.subTest(state=state):
                result = self.run_action(**options, request_id=state)
                self.assertEqual(result["state"], state)
                self.assertFalse(result["apply_command_attempted"])
                self.assertFalse(result["apply_command_sent"])
                self.assertNotIn("verification", result)
        self.assertTrue(all(0x4250 not in ops for ops in self.sessions))

    def test_same_request_never_replays_even_after_restart(self):
        first = self.run_action()
        count = len(self.sessions)
        second = self.run_action()
        self.assertEqual(second, {"state": "already_requested", "report": first})
        self.assertEqual(len(self.sessions), count)
        mismatch = self.run_action(expected_sha256="0" * 64)
        self.assertEqual(mismatch["state"], "target_mismatch")
        self.assertEqual(len(self.sessions), count)

    def test_new_id_does_not_bypass_apply_cooldown(self):
        self.run_action()
        count = len(self.sessions)
        result = self.run_action(request_id="x50-opt-002")
        self.assertEqual(result["state"], "apply_cooldown")
        self.assertGreater(result["remaining_seconds"], 1700)
        self.assertEqual(len(self.sessions), count)

    def test_ack_without_channel_change_does_not_invent_a_change(self):
        result = self.run_action(observed_change=False)
        self.assertEqual(result["conclusion"], "acknowledged_without_observed_channel_change")
        self.assertEqual(result["verification"]["radio_changes"], [])
        self.assertFalse(result["verification"]["channel_comparison"]["observed_channels_changed"])

    def test_uncertain_send_is_never_reapplied_even_if_readings_changed(self):
        result = self.run_action(fail_apply=True)
        self.assertEqual(result["state"], "apply_outcome_uncertain")
        self.assertTrue(result["apply_command_attempted"])
        self.assertFalse(result["apply_command_sent"])
        self.assertEqual(result["conclusion"], "unconfirmed_with_channel_change")
        self.assertEqual(sum(ops.count(0x4250) for ops in self.sessions), 1)
        count = len(self.sessions)
        self.assertEqual(self.run_action(request_id="new-id")["state"], "previous_apply_unverified")
        self.assertEqual(len(self.sessions), count)

    def test_rejected_or_malformed_ack_never_becomes_accepted_by_observation(self):
        for response, state in (({"error_code": -4104, "message": "PRIVATE"}, "apply_rejected"),
                                (b'{"error_code":true}', "apply_outcome_uncertain"),
                                (b'{"error_code":0,"error_code":1}', "apply_outcome_uncertain")):
            # A separate temporary journal models an independent installation.
            self.journal = Path(self.directory.name) / ("case-" + str(len(self.sessions)) + ".json")
            self.args["journal"] = str(self.journal)
            result = self.run_action(ack=response)
            self.assertEqual(result["state"], state)
            self.assertFalse(result["conclusion"].startswith("acknowledged_"))

    def test_observation_timeout_recovers_with_verify_only_no_scan_or_apply(self):
        result = self.run_action(observation_states=["timeout"] * 6)
        self.assertEqual(result["state"], "verification_incomplete")
        self.assertEqual(result["verification"]["attempts"], 6)
        self.assertEqual(self.run_action(request_id="new-id")["state"], "previous_apply_unverified")
        count = len(self.sessions)
        recovered = self.run_action(verify_only=True)
        self.assertEqual(recovered["conclusion"], "acknowledged_with_channel_change")
        self.assertIn("last_verified_at", recovered)
        self.assertEqual(self.sessions[count:], [[1, 0x40A0, 0x4009, 0x400F]] * 3)
        self.assertEqual(sum(ops.count(0x4250) for ops in self.sessions), 1)

    def test_connection_loss_resets_stability_and_auth_failure_stops(self):
        result = self.run_action(observation_states=[None, "connection_error", None, None, None])
        self.assertEqual(result["verification"]["attempts"], 5)
        self.assertEqual(result["verification"]["stable_samples"], 3)
        result = self.run_action(verify_only=True, observation_states=["authentication_rejected"])
        self.assertEqual(result["verification"]["attempts"], 1)
        self.assertEqual(result["verification"]["state"], "verification_blocked")
        self.assertEqual(result["state"], "verification_incomplete")

    def test_storage_failure_before_apply_prevents_the_write(self):
        real_save = probe.atomic_json
        calls = []
        def fail(path, doc):
            calls.append(1)
            if len(calls) == 2:
                raise probe.ActionStorageError()
            real_save(path, doc)
        with patch.object(probe, "atomic_json", side_effect=fail):
            result = self.run_action()
        self.assertEqual(result["state"], "action_storage_or_state_error")
        self.assertTrue(all(0x4250 not in ops for ops in self.sessions))
        count = len(self.sessions)
        self.assertEqual(self.run_action()["state"], "already_requested")
        self.assertEqual(len(self.sessions), count)

    def test_post_send_storage_failure_leaves_durable_unknown_attempt(self):
        real_save = probe.atomic_json
        calls = []
        def fail(path, doc):
            calls.append(1)
            if len(calls) == 3:
                raise probe.ActionStorageError()
            real_save(path, doc)
        with patch.object(probe, "atomic_json", side_effect=fail):
            result = self.run_action()
        self.assertEqual(result["state"], "action_storage_or_state_error")
        disk = json.loads(self.journal.read_text())["actions"]["x50-opt-001"]["report"]
        self.assertTrue(disk["apply_command_attempted"])
        self.assertEqual(disk["apply_ack"]["state"], "unavailable")
        self.assertEqual(self.run_action(request_id="another")["state"], "previous_apply_unverified")
        result = self.run_action(verify_only=True)
        self.assertEqual(result["conclusion"], "unconfirmed_with_channel_change")
        self.assertEqual(sum(ops.count(0x4250) for ops in self.sessions), 1)

    def test_locked_corrupt_or_missing_history_never_connects(self):
        with probe.action_store(self.journal):
            self.assertEqual(self.run_action()["state"], "action_in_progress")
        self.assertEqual(self.run_action(verify_only=True)["state"], "nothing_to_verify")
        for content in ('invalid', '{}', '{"version":1,"actions":[]}',
                        '{"version":1,"version":1,"actions":{}}'):
            self.journal.write_text(content)
            self.assertEqual(self.run_action()["state"], "action_storage_or_state_error")
        self.assertEqual(self.sessions, [])

    def test_interrupted_attempt_can_only_resume_verification(self):
        with patch.object(probe, "verify_application", side_effect=KeyboardInterrupt()):
            result = self.run_action()
        self.assertEqual(result["state"], "interrupted")
        self.assertTrue(result["apply_command_attempted"])
        self.assertEqual(self.run_action()["state"], "already_requested")
        self.assertEqual(self.run_action(verify_only=True)["verification"]["state"], "stable")
        self.assertEqual(sum(ops.count(0x4250) for ops in self.sessions), 1)

    def test_stop_at_durable_reservation_prevents_apply_and_never_replays(self):
        stopping = [False]
        def progress(report):
            if report['apply_command_attempted']:
                stopping[0] = True
        result = self.run_action(_progress=progress, _should_stop=lambda: stopping[0])
        self.assertEqual(result['state'], 'interrupted')
        self.assertTrue(result['apply_command_attempted'])
        self.assertTrue(all(0x4250 not in ops for ops in self.sessions))
        self.assertEqual(self.run_action(request_id='another')['state'], 'previous_apply_unverified')

    def test_stop_during_wait_preserves_ack_without_reapplying(self):
        result = self.run_action(_wait=lambda _: True)
        self.assertEqual(result['state'], 'interrupted')
        self.assertEqual(result['apply_ack']['state'], 'accepted')
        self.assertEqual(len(self.sessions), 1)
        self.assertEqual(self.run_action(verify_only=True)['verification']['state'], 'stable')
        self.assertEqual(sum(ops.count(0x4250) for ops in self.sessions), 1)

    def test_modes_need_explicit_id_and_low_level_apply_remains_disabled(self):
        with patch.object(probe.socket, "create_connection") as connect:
            with self.assertRaises(ValueError):
                probe.probe("192.168.68.1", optimize=True)
            connect.assert_not_called()
        channel = MagicMock()
        for allow_scan in (False, True):
            with self.assertRaisesRegex(probe.ProtocolError, "tmp_opcode_not_allowed"):
                probe.TMPReader(channel, allow_scan=allow_scan).request(0x4250, 60)
        channel.send.assert_not_called()
        for flags in (("--optimize",), ("--verify-only",), ("--optimize", "--scan-only")):
            with patch.object(probe.sys, "argv", ["probe", "192.168.68.1", *flags]), \
                    patch.object(probe.getpass, "getpass") as prompt, patch("sys.stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit):
                    probe.main()
                prompt.assert_not_called()

    def test_apply_one_use_gate_blocks_second_write_even_after_send_failure(self):
        channel = MagicMock()
        channel.report = {}
        channel.send.side_effect = TimeoutError()
        reader = probe.TMPReader(channel)
        reservation = MagicMock()
        reader.reserve_apply = reservation
        with self.assertRaises(TimeoutError):
            reader.request(0x4250, 60)
        with self.assertRaisesRegex(probe.ProtocolError, "apply_already_attempted"):
            reader.request(0x4250, 60)
        reservation.assert_called_once()
        channel.send.assert_called_once()

    def test_atomic_storage_failure_does_not_overwrite_previous_report(self):
        probe.atomic_json(self.journal, {"before": True})
        with patch.object(probe.os, "replace", side_effect=OSError()):
            with self.assertRaises(probe.ActionStorageError):
                probe.atomic_json(self.journal, {"after": True})
        self.assertEqual(json.loads(self.journal.read_text()), {"before": True})
        self.assertEqual(list(Path(self.directory.name).iterdir()), [self.journal])

    def test_corrupt_apply_metadata_fails_before_network(self):
        self.run_action()
        original = json.loads(self.journal.read_text())
        count = len(self.sessions)
        for field, value in (("apply_ack", []), ("verification", "stable"),
                             ("apply_attempted_at", float("nan")), ("channels_before_apply", {})):
            document = json.loads(json.dumps(original))
            document["actions"]["x50-opt-001"]["report"][field] = value
            self.journal.write_text(json.dumps(document))
            self.assertEqual(self.run_action(verify_only=True)["state"], "action_storage_or_state_error")
        self.assertEqual(len(self.sessions), count)

    def test_cli_application_and_verification_preserve_local_credentials(self):
        for flag in ("--optimize", "--verify-only"):
            args = ["probe", "192.168.68.1", "--expected-sha256", AuthenticationProbeTests.PIN,
                    flag, "--request-id", "x50-opt-001", "--journal", str(self.journal), "--timeout", "20"]
            with patch.object(probe.sys, "argv", args), patch.object(probe.sys.stdin, "isatty", return_value=True), \
                    patch.object(probe.getpass, "getpass", side_effect=["owner@example.test", " secret "]), \
                    patch.object(probe, "run_action", return_value={"state": "apply_acknowledged",
                        "conclusion": "acknowledged_with_channel_change", "verification": {"state": "stable"}}) as run, \
                    patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(probe.main(), 0)
                self.assertEqual(run.call_args.args[3], " secret ")
                self.assertEqual(run.call_args.kwargs["verify_only"], flag == "--verify-only")
