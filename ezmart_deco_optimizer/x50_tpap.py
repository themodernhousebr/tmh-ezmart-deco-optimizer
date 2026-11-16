#!/usr/bin/env python3
"""Shared X50 TLS/TMP client, with an optional standalone diagnostic CLI.

Only --optimize can apply channels, after a positive analysis and durable reservation.
--verify-only observes a journaled attempt without scanning or applying again.

Python 3.9+; authenticated modes require PyCryptodome. The public CA comes from the official
X50 1.8.4 firmware documented in RESEARCH.md. It is scoped to this connection;
it is never installed in the operating system's trust store.
"""

import argparse
import base64
import binascii
from contextlib import contextmanager
import fcntl
import getpass
import hashlib
import hmac
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import stat
import struct
import sys
import tempfile
import time
import warnings
import zlib


PORT = 30001
HEADER = struct.Struct(">BBBBI8s4sI")
CRC_SEED = 0x92BF30ED
MAX_BODY = 8192
TMP_CHANNEL = b"TMP\0\0\0\0\0"
MAX_CHANNEL_BODY = 65535 + 16 + 16  # TMP business + transport + CCM tag.
MAX_TMP_RESULT = 262144
READ_OPS = {"mode": 0x40A0, "channels": 0x4009, "devices": 0x400F}
ACS_SCAN = 0x424F
ACS_APPLY = 0x4250
P256_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
# RFC 9383 P-256 points, also present verbatim in the X50 1.8.4 library.
SPAKE_M = bytes.fromhex(
    "04886e2f97ace46e55ba9dd7242579f2993b64e16ef3dcab95afd497333d8fa12f5f"
    "f355163e43ce224e0b0e65ff02ac8e5c7be09419c785e0ca547d55a12e2d20")
SPAKE_N = bytes.fromhex(
    "04d8bbd6c639c62937b04d997f38c3770719c629d7014d49a24b4f98baa1292b49"
    "07d60aa6bfade45008a636337f5168c64d9bd36034808cd564490b1e656edbe7")
ROOT_CA = """-----BEGIN CERTIFICATE-----
MIICNzCCAdygAwIBAgIUNLD7w5j5WU/efCe8bqkfGSRGgLYwCgYIKoZIzj0EAwIw
ezEnMCUGA1UEAwweVFAtTElOSyBTWVNURU1TIERFVklDRSBST09UIENBMR0wGwYD
VQQKDBRUUC1MSU5LIFNZU1RFTVMgSU5DLjEPMA0GA1UEBwwGSXJ2aW5lMRMwEQYD
VQQIDApDYWxpZm9ybmlhMQswCQYDVQQGEwJVUzAgFw0yNDExMjIwMjU3NDhaGA8y
MDU0MTExNTAyNTc0OFowezEnMCUGA1UEAwweVFAtTElOSyBTWVNURU1TIERFVklD
RSBST09UIENBMR0wGwYDVQQKDBRUUC1MSU5LIFNZU1RFTVMgSU5DLjEPMA0GA1UE
BwwGSXJ2aW5lMRMwEQYDVQQIDApDYWxpZm9ybmlhMQswCQYDVQQGEwJVUzBZMBMG
ByqGSM49AgEGCCqGSM49AwEHA0IABLwo8H9H6BoJDvcoewi4wPrPryVXir4z4yXV
n29R5XCAcFfKk06pYPupG6pjaKOLKWXnaOdPZThDFxwGLo3urV2jPDA6MAsGA1Ud
DwQEAwIBhjAMBgNVHRMEBTADAQH/MB0GA1UdDgQWBBRivfUtiHYsZBOKo80uZEwk
XhBkdDAKBggqhkjOPQQDAgNJADBGAiEA+7j5jemtXcGYN0unH+9rjVhVAL7WrsOi
5rbc0IIvD6MCIQCZuGGssu4Ygt2V8Vr0QF2fO9wxfNB3aRRMYQ+6lMrLGA==
-----END CERTIFICATE-----
"""


def make_context():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # Deco certificates name the product, not its DHCP/private IP address.
    # Require the vendor chain, check the product below, and optionally pin
    # the individual certificate. Do not claim IP/hostname verification.
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_verify_locations(cadata=ROOT_CA)
    return context


class ProtocolError(Exception):
    """Only fixed diagnostic codes, never raw packet contents."""


def auth_packet(request):
    sequence = secrets.token_bytes(4)
    body = json.dumps(request, separators=(",", ":")).encode("utf-8")
    packet = HEADER.pack(1, 1, 1, 0, len(body), b"\0" * 8, sequence, CRC_SEED) + body
    return packet[:20] + struct.pack(">I", zlib.crc32(packet)) + body, sequence


def registration_packet(owner_email, user_random=None):
    """Firmware prover_SendParamRequest: one existing-account negotiation."""
    if user_random is None:
        user_random = secrets.token_bytes(32)
    request = {"method": "login", "params": {
        "sub_method": "pake_register",
        "username": hashlib.sha256(owner_email.encode("utf-8")).hexdigest(),
        "user_random": base64.b64encode(user_random).decode("ascii"),
        "passcode_type": "userpw",
        "cipher_suites": [1],
        "encryption": ["aes_128_ccm"],
    }}
    return auth_packet(request)


def remaining_timeout(connection, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError()
    connection.settimeout(remaining)


def receive_exact(connection, length, deadline):
    data = bytearray()
    while len(data) < length:
        remaining_timeout(connection, deadline)
        chunk = connection.recv(length - len(data))
        if not chunk:
            raise ProtocolError("response_truncated")
        data.extend(chunk)
    return bytes(data)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("duplicate_json_key")
        result[key] = value
    return result


def receive_registration(connection, sequence, deadline):
    header = receive_exact(connection, HEADER.size, deadline)
    version, flags, kind, reserved, length, channel, response_sequence, crc = HEADER.unpack(header)
    if (version, flags, kind, reserved, channel) != (1, 2, 1, 0, b"\0" * 8):
        raise ProtocolError("unexpected_header")
    if response_sequence != sequence:
        raise ProtocolError("sequence_mismatch")
    if not 0 < length <= MAX_BODY:
        raise ProtocolError("invalid_response_length")
    body = receive_exact(connection, length, deadline)
    if zlib.crc32(header[:20] + struct.pack(">I", CRC_SEED) + body) != crc:
        raise ProtocolError("checksum_mismatch")
    try:
        return json.loads(body.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeError, ValueError, RecursionError):
        raise ProtocolError("invalid_json") from None


def api_code(message):
    if not isinstance(message, dict) or type(message.get("error_code")) is not int:
        raise ProtocolError("invalid_response_shape")
    code = message["error_code"]
    if not -(2**31) <= code < 2**31:
        raise ProtocolError("invalid_api_code")
    return code


def decoded_parameter(value, minimum, maximum):
    try:
        data = base64.b64decode(value, validate=True) if isinstance(value, str) else b""
    except (ValueError, binascii.Error):
        raise ProtocolError("invalid_parameter_encoding") from None
    if not minimum <= len(data) <= maximum:
        raise ProtocolError("invalid_parameter_length")
    return data


def summarize_parameters(message):
    """Do not export salts, points, nonces, account hashes, tokens or raw JSON."""
    code = api_code(message)
    if code != 0:
        return {"state": "pake_rejected", "api_code": code}
    params = message.get("result")
    if not isinstance(params, dict) or params.get("sub_method") != "pake_register":
        raise ProtocolError("unexpected_auth_method")
    if type(params.get("cipher_suites")) is not int or params["cipher_suites"] != 1:
        raise ProtocolError("unexpected_cipher_suite")
    if params.get("encryption") != "aes_128_ccm":
        raise ProtocolError("unexpected_encryption")
    iterations = params.get("iterations")
    if type(iterations) is not int or not 1 <= iterations <= 10000000:
        raise ProtocolError("invalid_iterations")
    sizes = {}
    for name, minimum, maximum in [("dev_random", 32, 32), ("dev_salt", 1, 64),
                                   ("dev_share", 65, 65)]:
        data = decoded_parameter(params.get(name), minimum, maximum)
        if name == "dev_share" and data[0] != 4:
            raise ProtocolError("unexpected_point_encoding")
        sizes[name + "_bytes"] = len(data)
    extra = params.get("extra_crypt")
    extra_summary = {"present": extra is not None}
    if extra is not None:
        if not isinstance(extra, dict):
            raise ProtocolError("invalid_extra_crypt")
        kind = extra.get("type")
        extra_summary["type"] = kind if kind in (
            "password_shadow", "password_sha_with_salt") else "unrecognized"
        options = extra.get("params")
        if isinstance(options, dict):
            for name in ("passwd_id", "sha_name", "rounds"):
                value = options.get(name)
                if type(value) is int and 0 <= value <= 10000000:
                    extra_summary[name] = value
    return {"state": "pake_parameters_received", "api_code": 0,
            "cipher_suite": 1, "encryption": "aes_128_ccm", "iterations": iterations,
            "parameter_sizes": sizes, "extra_crypt": extra_summary,
            # Firmware also generates plausible parameters for unknown users.
            "owner_account_verified": False}


def crypto_modules():
    try:
        from Crypto.PublicKey import ECC
        from Crypto.Protocol.KDF import HKDF
        from Crypto.Hash import SHA256
    except ImportError:
        raise ValueError("Para login/leitura, instale py3-pycryptodome no Terminal/SSH.") from None
    return ECC, HKDF, SHA256


def encode_point(point):
    if point.is_point_at_infinity():
        raise ProtocolError("invalid_curve_point")
    return b"\x04" + int(point.x).to_bytes(32, "big") + int(point.y).to_bytes(32, "big")


def decode_point(encoded):
    ECC, _, _ = crypto_modules()
    if len(encoded) != 65 or encoded[0] != 4:
        raise ProtocolError("unexpected_point_encoding")
    try:
        point = ECC.import_key(encoded, curve_name="P-256").pointQ
    except ValueError:
        raise ProtocolError("invalid_curve_point") from None
    if point.is_point_at_infinity():
        raise ProtocolError("invalid_curve_point")
    return point


def _spake2p_proofs(context, w0, w1, scalar, server_share, prover_id=b"", verifier_id=b""):
    """RFC 9383 proof calculation; explicit scalar permits published-vector tests.

    Production callers always use a fresh secrets-generated scalar. P-256 has
    cofactor 1; validated nonidentity points belong to its prime-order group.
    No private values or session keys are returned in the diagnostic report.
    """
    ECC, HKDF, SHA256 = crypto_modules()
    if any(not 0 < value < P256_ORDER for value in (w0, w1, scalar)):
        raise ProtocolError("invalid_scalar")
    peer = decode_point(server_share)
    m, n = decode_point(SPAKE_M), decode_point(SPAKE_N)
    client = ECC.construct(curve="P-256", d=scalar).pointQ + m * w0
    unmasked = peer + -(n * w0)
    if unmasked.is_point_at_infinity():
        raise ProtocolError("invalid_curve_point")
    client_share = encode_point(client)
    transcript = b"".join(struct.pack("<Q", len(part)) + part for part in (
        context, prover_id, verifier_id, SPAKE_M, SPAKE_N, client_share, server_share,
        encode_point(unmasked * scalar), encode_point(unmasked * w1), w0.to_bytes(32, "big")))
    main_key = hashlib.sha256(transcript).digest()
    confirmation_keys = HKDF(main_key, 64, b"", SHA256, context=b"ConfirmationKeys")
    user_confirm = hmac.digest(confirmation_keys[:32], server_share, "sha256")
    dev_confirm = hmac.digest(confirmation_keys[32:], client_share, "sha256")
    shared_key = HKDF(main_key, 32, b"", SHA256, context=b"SharedKey")
    return client_share, user_confirm, dev_confirm, shared_key


def password_proofs(params, owner_email, password, user_random):
    # Only the exact profile observed on the client X50; never guess alternative
    # password encodings or automatically retry a rejected proof.
    if params.get("extra_crypt") is not None:
        raise ProtocolError("unsupported_password_profile")
    if not 1 <= params["iterations"] <= 1000000:
        raise ProtocolError("authentication_iteration_limit")
    server_share = decoded_parameter(params.get("dev_share"), 65, 65)
    decode_point(server_share)  # Validate before doing the expensive PBKDF.
    server_random = decoded_parameter(params.get("dev_random"), 32, 32)
    salt = decoded_parameter(params.get("dev_salt"), 32, 32)
    # accountmgnt.lua:set_tpap_authmgmt passes decoded username + '/' + decoded
    # password to libtpap.generate_verify. Keep case and password whitespace.
    material = (owner_email + "/" + password).encode("utf-8")
    derived = hashlib.pbkdf2_hmac("sha256", material, salt, params["iterations"], dklen=80)
    w0 = int.from_bytes(derived[:40], "big") % P256_ORDER
    w1 = int.from_bytes(derived[40:], "big") % P256_ORDER
    context = hashlib.sha256(b"PAKE V1" + user_random + server_random).digest()
    scalar = secrets.randbelow(P256_ORDER - 1) + 1
    return _spake2p_proofs(context, w0, w1, scalar, server_share)


def authenticate(tls, deadline, params, owner_email, password, user_random, result):
    share, proof, expected, shared_key = password_proofs(params, owner_email, password, user_random)
    packet, sequence = auth_packet({"method": "login", "params": {
        "sub_method": "pake_share", "user_share": base64.b64encode(share).decode("ascii"),
        "user_confirm": base64.b64encode(proof).decode("ascii"),
    }})
    remaining_timeout(tls, deadline)
    sent = result["application_bytes_sent"]
    result.update(authentication="proof_attempted", password_proof_attempted=True,
                  application_bytes_sent=None)
    tls.sendall(packet)
    result.update(password_proof_sent=True, application_bytes_sent=sent + len(packet),
                  application_messages_sent=2)
    message = receive_registration(tls, sequence, deadline)
    code = api_code(message)
    result["api_code"] = code
    if code != 0:
        result.update(state="authentication_rejected", authentication="rejected")
        return
    reply = message.get("result")
    if not isinstance(reply, dict) or reply.get("sub_method") != "pake_share":
        raise ProtocolError("unexpected_auth_method")
    received = decoded_parameter(reply.get("dev_confirm"), 32, 32)
    if not hmac.compare_digest(expected, received):
        result.update(state="server_proof_mismatch", authentication="not_verified")
        return
    result["server_proof_verified"] = True
    # Native prover_HandleShareResponse requires stok. Do not export or save it.
    token = reply.get("stok")
    if not isinstance(token, str) or not 1 <= len(token) <= 256:
        raise ProtocolError("invalid_session_token")
    result.update(state="authentication_verified", authentication="verified",
                  account_credentials_verified=True, session_token_received=True)
    return shared_key  # In memory only, released to the caller AFTER mutual proof.


class TMPChannel:
    """Firmware TPAP type-2 TMP byte stream, bound to the authenticated TLS socket."""

    def __init__(self, tls, shared_key, report):
        from Crypto.Cipher import AES
        _, HKDF, SHA256 = crypto_modules()
        self.AES = AES
        self.key = HKDF(shared_key, 32, b"tp-kdf-salt-aes128-key", SHA256,
                        context=b"tp-kdf-info-aes128-key")[:16]
        self.iv = HKDF(shared_key, 32, b"tp-kdf-salt-aes128-iv", SHA256,
                       context=b"tp-kdf-info-aes128-iv")[:8]
        self.tls, self.report = tls, report
        self.sequence = secrets.randbelow(2**32 - 1024)
        self.seen = set()
        self.buffer = bytearray()
        self.deadline = 0
        self.received_bytes = 0
        self.sent_frames = 0

    def send(self, plaintext):
        if self.sent_frames >= 1024 or not 0 < len(plaintext) <= MAX_CHANNEL_BODY - 16:
            raise ProtocolError("channel_send_limit")
        sequence = struct.pack("<I", self.sequence)
        self.sequence += 1  # Never reuse a nonce, even after an uncertain send.
        self.sent_frames += 1
        cipher = self.AES.new(self.key, self.AES.MODE_CCM, nonce=self.iv + sequence, mac_len=16)
        encrypted, tag = cipher.encrypt_and_digest(plaintext)
        body = encrypted + tag
        # Native channel frames use flag 1 in BOTH directions; login replies use 2.
        header = HEADER.pack(1, 1, 2, 0, len(body), TMP_CHANNEL, sequence, CRC_SEED)
        packet = header[:20] + struct.pack(">I", zlib.crc32(header + body)) + body
        remaining_timeout(self.tls, self.deadline)
        sent = self.report["application_bytes_sent"]
        self.report["application_bytes_sent"] = None
        self.tls.sendall(packet)
        self.report["application_bytes_sent"] = sent + len(packet)
        self.report["application_messages_sent"] += 1

    def exact(self, size):
        while len(self.buffer) < size:
            header = receive_exact(self.tls, HEADER.size, self.deadline)
            version, flags, kind, reserved, length, channel, sequence, crc = HEADER.unpack(header)
            if (version, flags, kind, reserved, channel) != (1, 1, 2, 0, TMP_CHANNEL):
                raise ProtocolError("unexpected_channel_header")
            if not 16 < length <= MAX_CHANNEL_BODY:
                raise ProtocolError("invalid_channel_length")
            if sequence in self.seen:
                raise ProtocolError("repeated_channel_sequence")
            if len(self.seen) >= 1024 or self.received_bytes + length > 2 * 1024 * 1024:
                raise ProtocolError("channel_receive_limit")
            body = receive_exact(self.tls, length, self.deadline)
            if zlib.crc32(header[:20] + struct.pack(">I", CRC_SEED) + body) != crc:
                raise ProtocolError("channel_checksum_mismatch")
            cipher = self.AES.new(self.key, self.AES.MODE_CCM, nonce=self.iv + sequence, mac_len=16)
            try:
                plaintext = cipher.decrypt_and_verify(body[:-16], body[-16:])
            except ValueError:
                raise ProtocolError("channel_tag_mismatch") from None
            self.seen.add(sequence)
            self.received_bytes += length
            self.buffer.extend(plaintext)
            self.report["channel_frames_received"] += 1
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data


class TMPReader:
    """Fixed reads; ACS analysis/application require separate one-use gates."""

    def __init__(self, channel, allow_scan=False):
        self.channel = channel
        self.sequence = 0
        self.transaction = -1
        self.allow_scan = allow_scan
        self.scan_transaction = None
        self.reserve_apply = None
        self.apply_transaction = None
        self.allow_guest = False
        self.guest_transaction = None

    def send(self, opcode, body, offset=0, pull=False):
        if opcode == ACS_SCAN and self.allow_scan:
            if pull:
                if self.scan_transaction != self.transaction:
                    raise ProtocolError("scan_pull_without_request")
            elif self.scan_transaction is not None:
                raise ProtocolError("scan_already_attempted")
            else:
                # Reserve before I/O: a partial write must never be retried.
                self.scan_transaction = self.transaction
        elif opcode == ACS_APPLY and self.reserve_apply is not None:
            if pull:
                if self.apply_transaction != self.transaction:
                    raise ProtocolError("apply_pull_without_request")
            elif self.apply_transaction is not None:
                raise ProtocolError("apply_already_attempted")
            else:
                self.apply_transaction = self.transaction
                self.reserve_apply()  # Must finish durable storage before any bytes.
        elif opcode == 0x4008 and self.allow_guest:
            from guest_wifi import validate_params
            if not pull:
                document = json.loads(body, object_pairs_hook=unique_object)
                if set(document) != {'config_version', 'params'}:
                    raise ProtocolError('guest_payload_not_allowed')
                validate_params(document['params'])
            if pull:
                if self.guest_transaction != self.transaction:
                    raise ProtocolError('guest_pull_without_request')
            elif self.guest_transaction is not None:
                raise ProtocolError('guest_already_attempted')
            else:
                self.guest_transaction = self.transaction
        elif opcode not in (1, *READ_OPS.values()):
            raise ProtocolError("tmp_opcode_not_allowed")
        self.sequence += 1
        business = struct.pack(">BBHBBHIII", 1, 2, opcode, 4 if pull else 2, 0,
                               self.transaction, zlib.crc32(body), len(body), offset) + body
        header = struct.pack(">BBBBHHI", 1, 1, 5, 0, len(business), 0, self.sequence)
        crc = zlib.crc32(header + bytes.fromhex("5a6b7c8d") + business)
        is_scan = opcode == ACS_SCAN and not pull
        if is_scan:
            self.channel.report["scan"].update(state="response_pending", request_attempted=True)
        self.channel.send(header + struct.pack(">I", crc) + business)
        if is_scan:
            self.channel.report["scan"]["request_sent"] = True
        if opcode == ACS_APPLY and not pull:
            self.channel.report["apply_command_sent"] = True

    def guest_request(self, opcode, params=None):
        from guest_wifi import READ_OPS as GUEST_READ_OPS, WRITE_WLAN, validate_params
        if not self.allow_guest or opcode not in (*GUEST_READ_OPS, WRITE_WLAN):
            raise ProtocolError('guest_opcode_not_allowed')
        if opcode == WRITE_WLAN:
            validate_params(params)
        elif params is not None:
            raise ProtocolError('guest_read_params_not_allowed')
        return self._exchange(opcode, 20, params or {})

    def request(self, opcode, timeout):
        if opcode == 0x4008:
            raise ProtocolError('tmp_opcode_not_allowed')
        return self._exchange(opcode, timeout, {})

    def _exchange(self, opcode, timeout, params):
        self.channel.deadline = time.monotonic() + timeout
        self.transaction += 1
        body = b"" if opcode == 1 else json.dumps({
            "config_version": int(time.time() * 1000), "params": params
        }, separators=(",", ":")).encode()
        self.send(opcode, body)
        result = bytearray()
        expected_total = None
        whole_crc = None
        for _ in range(512):
            prefix = self.channel.exact(4)
            if prefix == b"\x01\x01\x04\x00":
                self.channel.send(prefix)  # TMP keepalive, not another operation.
                continue
            if prefix == b"\x01\x01\x02\x00":
                continue
            if prefix != b"\x01\x01\x05\x00":
                raise ProtocolError("unexpected_tmp_header")
            header = prefix + self.channel.exact(12)
            size = struct.unpack_from(">H", header, 4)[0]
            if size < 20:
                raise ProtocolError("invalid_tmp_length")
            payload = self.channel.exact(size)
            if zlib.crc32(header[:12] + bytes.fromhex("5a6b7c8d") + payload) != struct.unpack_from(">I", header, 12)[0]:
                raise ProtocolError("tmp_checksum_mismatch")
            version, subversion, returned, kind, error, tx, crc, total, offset = struct.unpack_from(">BBHBBHIII", payload)
            chunk = payload[20:]
            fragmented_whole_crc = (version, subversion) == (1, 2) and (
                whole_crc is not None or (not result and total > len(chunk) > 0 and offset == 0))
            if ((version, subversion) != (1, 2)
                    or (whole_crc is not None and crc != whole_crc)
                    or (whole_crc is None and zlib.crc32(chunk) != crc and not fragmented_whole_crc)):
                # Numeric framing metadata only, no body, CRC value or device identifiers.
                self.channel.report['tmp_business_diagnostic'] = {
                    'version': version, 'subversion': subversion,
                    'opcode': returned, 'kind': kind, 'error': error,
                    'chunk_bytes': len(chunk), 'total_bytes': total, 'offset': offset,
                    'version_matches': (version, subversion) == (1, 2),
                    'chunk_crc_matches': zlib.crc32(chunk) == crc,
                    'assembled_crc_matches': zlib.crc32(bytes(result) + chunk) == crc,
                }
                raise ProtocolError("tmp_business_invalid")
            if (returned, kind, tx) != (opcode, 5, self.transaction):
                raise ProtocolError("tmp_response_mismatch")
            if error:
                self.channel.report["tmp_error_code"] = error
                raise ProtocolError("tmp_request_rejected")
            if (total > MAX_TMP_RESULT or offset != len(result)
                    or (expected_total is not None and total != expected_total)):
                raise ProtocolError("tmp_fragment_invalid")
            if whole_crc is None and zlib.crc32(chunk) != crc:
                # Some devices advertise the complete message CRC in every fragment.
                # Outer TMP CRC and authenticated CCM still protect each fragment.
                whole_crc = crc
            expected_total = total
            result.extend(chunk)
            if len(result) > MAX_TMP_RESULT or (total and len(result) > total):
                raise ProtocolError("tmp_result_too_large")
            if total == 0 or len(result) == total:
                if whole_crc is not None and zlib.crc32(result) != whole_crc:
                    raise ProtocolError('tmp_message_checksum_mismatch')
                return bytes(result)
            if not chunk:
                raise ProtocolError("tmp_empty_fragment")
            self.send(opcode, b"", offset=len(result), pull=True)
        raise ProtocolError("tmp_frame_limit")


def project_read(name, body):
    """Whitelist product metadata and channel numbers; never export raw WLAN JSON."""
    try:
        message = json.loads(body.decode("utf-8"), object_pairs_hook=unique_object)
    except (ValueError, UnicodeError, RecursionError):
        raise ProtocolError("invalid_tmp_json") from None
    code = api_code(message)
    if code:
        return {"state": "api_error", "api_code": code}
    data = message.get("result")
    if not isinstance(data, dict):
        return {"state": "unrecognized_response", "api_code": 0}
    out = {}
    if name == "mode":
        mode = data.get("mode")
        if mode in ("Router", "AP", "FAP", "HAP", "RE", "LTE", "Mobile_5G", "Mobile_Bridge"):
            out["mode"] = mode
    elif name == "devices":
        devices = data.get("device_list")
        if isinstance(devices, list) and 0 < len(devices) <= 64:
            nodes = []
            for device in devices:
                if not isinstance(device, dict):
                    continue
                node = {}
                for source, target, pattern in (
                    ("device_model", "model", r"(?:Deco )?[A-Z]{1,3}[0-9]{1,4}(?:Plus|R|S)?(?:\([A-Z]{2}\))?"),
                    ("hardware_ver", "hardware", r"[0-9]{1,2}(?:\.[0-9]{1,2}){0,2}"),
                    ("software_ver", "firmware", r"[0-9.]{3,20}(?: Build [0-9]{6,10} Rel[. ]{0,2}[0-9]{1,6})?"),
                ):
                    value = device.get(source)
                    if isinstance(value, str) and re.fullmatch(pattern, value):
                        node[target] = value
                if device.get("role") in ("master", "slave"):
                    node["role"] = device["role"]
                if "model" in node:
                    nodes.append(node)
            if nodes:
                out = {"node_count": len(devices), "nodes": nodes}
    elif name == "channels":
        for band in ("band2_4", "band5_1", "band5_2", "band6", "band6_2"):
            config = data.get(band)
            if not isinstance(config, dict):
                continue
            sections = {}
            for section in ("radio", "host", "backhaul"):
                values = config.get(section)
                if not isinstance(values, dict):
                    continue
                clean = {}
                channel = values.get("channel")
                if isinstance(channel, str) and re.fullmatch(r"[0-9]{1,3}", channel):
                    channel = int(channel)
                if type(channel) is int and 1 <= channel <= 233:
                    clean["channel"] = channel
                for key in ("htmode", "channel_width", "bandwidth"):
                    width = values.get(key)
                    if isinstance(width, str) and re.fullmatch(r"(?:HT|VHT|HE|EHT)?(?:20|40|80|160|240|320)", width):
                        clean[key] = width
                if clean:
                    sections[section] = clean
            if any("channel" in value for value in sections.values()):
                out[band] = sections
    return {"state": "available" if out else "unrecognized_response", "api_code": 0, "data": out}


def read_information(tls, shared_key, timeout, result, allow_scan=False, include_guest=False):
    result.update(stage="tmp_allocate", tmp_session_verified=False,
                  channel_frames_received=0, reads={})
    reader = TMPReader(TMPChannel(tls, shared_key, result), allow_scan=allow_scan)
    token = reader.request(1, timeout)
    if not re.fullmatch(b"[0-9a-fA-F]{32}", token):
        raise ProtocolError("invalid_tmp_session")
    del token
    result["tmp_session_verified"] = True
    for name, opcode in READ_OPS.items():
        result["stage"] = "read_" + name
        body = reader.request(opcode, timeout)
        result["reads"][name] = project_read(name, body)
        if include_guest and name == 'channels':
            import guest_wifi
            document = json.loads(body, object_pairs_hook=unique_object)
            if api_code(document) == 0 and isinstance(document.get('result'), dict):
                result['guest'] = guest_wifi.project(document['result'])
        del body
    complete = all(read["state"] == "available" for read in result["reads"].values())
    result.update(state="readonly_verified" if complete else "readonly_incomplete", stage="complete")
    return reader


def project_scan(body):
    """The 1.8.4 ACS contract does not prove freshness, even after a long wait."""
    try:
        message = json.loads(body.decode("utf-8"), object_pairs_hook=unique_object)
    except (ValueError, UnicodeError, RecursionError):
        raise ProtocolError("invalid_tmp_json") from None
    code = api_code(message)
    if code:
        return {"state": "scan_rejected", "api_code": code}
    data = message.get("result")
    if not isinstance(data, dict):
        return {"state": "scan_response_unrecognized", "api_code": 0}
    needed = data.get("need_optimize")
    items = data.get("optimize_item")
    channel = items.get("channel") if isinstance(items, dict) else None
    if type(needed) is not bool or type(channel) is not bool or needed != channel:
        return {"state": "scan_response_unrecognized", "api_code": 0}
    out = {"state": "scan_response_received", "api_code": 0,
           "need_optimize": needed, "optimize_item": {"channel": channel}}
    estimated = data.get("optimize_time")
    if type(estimated) is int and 0 <= estimated <= 600:
        out["estimated_apply_seconds"] = estimated
    return out


def compare_channels(before, after):
    """Compare only matching scopes; a backhaul number is not a host radio reading."""
    def numbers(snapshot):
        if snapshot.get("state") != "available":
            return {}
        return {(band, section): values["channel"]
                for band, sections in snapshot["data"].items()
                for section, values in sections.items() if "channel" in values}

    left, right = numbers(before), numbers(after)
    common = sorted(left.keys() & right.keys())
    changes = [{"band": b, "section": s, "before": left[b, s], "after": right[b, s]}
               for b, s in common if left[b, s] != right[b, s]]
    return {"state": "complete" if left and left.keys() == right.keys() else
            "partial" if common else "unavailable",
            "compared_fields": [{"band": b, "section": s} for b, s in common],
            "channel_changes": changes,
            "observed_channels_changed": bool(changes) if common else None}


X50_PROFILES = frozenset({
    ('1.30', '1.8.4 Build 26051413 Rel. 27460'),
    ('1.0', '1.11.1 Build 26081714 Rel. 32981'),
})


def supported_profile(model, hardware, firmware, mode):
    # Exact pairs only: do not infer equivalence across revisions or later builds.
    return (model == 'X50' and mode == 'Router' and isinstance(hardware, str)
            and isinstance(firmware, str) and (hardware, firmware) in X50_PROFILES)


def supported_target(result):
    reads = result["reads"]
    masters = [node for node in reads["devices"]["data"]["nodes"] if node.get("role") == "master"]
    if len(masters) != 1:
        return False
    node = masters[0]
    return supported_profile(node.get('model'), node.get('hardware'), node.get('firmware'),
                             reads['mode']['data'].get('mode'))


def scan_information(reader, timeout, scan_timeout, result):
    """One explicitly selected ACS analysis on the physically identified profile."""
    result["stage"] = "scan_preflight"
    if result["state"] != "readonly_verified":
        result.update(state="scan_preflight_failed")
        return
    if not supported_target(result):
        result.update(state="scan_target_unsupported")
        return
    reads = result["reads"]
    scan = result["scan"]
    result["stage"] = "scan"
    started = time.monotonic()
    try:
        body = reader.request(ACS_SCAN, scan_timeout)
        scan.update(response_received=True, state="scan_response_unrecognized")
        scan.update(project_scan(body))
    finally:
        scan["response_seconds"] = round(time.monotonic() - started, 3)
        if scan["state"] == "response_pending":
            scan["state"] = "response_unknown"
    result["state"] = scan["state"]
    # This is an observation, not an application acknowledgement or proof of improvement.
    result["stage"] = "read_channels_after_scan"
    result["channels_after_scan"] = project_read("channels", reader.request(READ_OPS["channels"], timeout))
    result["channel_comparison"] = compare_channels(reads["channels"], result["channels_after_scan"])
    if result["state"] == "scan_response_received" and result["channel_comparison"]["state"] != "complete":
        result["state"] = "scan_observation_incomplete"
    result["stage"] = "complete"


def apply_information(reader, apply_timeout, result, checkpoint):
    if result["state"] != "scan_response_received":
        return
    if result["scan"].get("need_optimize") is not True:
        result["state"] = "optimization_not_needed"
        return
    result.update(state="ready_to_apply", stage="apply",
                  channels_before_apply=result["channels_after_scan"])

    def reserve():
        result.update(state="apply_outcome_uncertain", apply_command_attempted=True,
                      apply_attempted_at=int(time.time()), apply_ack={"state": "unavailable"})
        checkpoint(result)

    reader.reserve_apply = reserve
    started = time.monotonic()
    try:
        body = reader.request(ACS_APPLY, apply_timeout)
        try:
            document = json.loads(body.decode("utf-8"), object_pairs_hook=unique_object)
        except (ValueError, UnicodeError, RecursionError):
            raise ProtocolError("invalid_apply_json") from None
        code = api_code(document)
        accepted = code == 0
        result.update(state="apply_acknowledged" if accepted else "apply_rejected",
                      optimization="acknowledged" if accepted else "rejected",
                      apply_ack={"state": "accepted" if accepted else "rejected", "api_code": code})
    except (ProtocolError, OSError) as error:
        result.update(state="apply_outcome_uncertain", optimization="unconfirmed")
        result["apply_error"] = str(error) if isinstance(error, ProtocolError) else "transport_error"
    finally:
        reader.reserve_apply = None
        result["apply_response_seconds"] = round(time.monotonic() - started, 3)
    checkpoint(result)


def probe(host, timeout=8, expected_sha256=None, owner_email=None, password=None,
          read_info=False, scan_only=False, scan_timeout=120, optimize=False,
          apply_timeout=60, _checkpoint=None, _report=None,
          _before_connect=None, _login_verified=None, _guest_callback=None, _guest_read=False):
    # Literal unicast IPs avoid DNS delays and accidental broadcast discovery.
    address = ipaddress.ip_address(host)
    if address.is_unspecified or address.is_multicast:
        raise ValueError("Use o IP do Deco principal.")
    if not 1 <= timeout <= 30:
        raise ValueError("O prazo deve estar entre 1 e 30 segundos.")
    if type(scan_timeout) is not int or not 30 <= scan_timeout <= 180:
        raise ValueError("O prazo da análise deve estar entre 30 e 180 segundos.")
    if sum(bool(mode) for mode in (read_info, scan_only, optimize)) > 1:
        raise ValueError("Selecione apenas um modo de operação.")
    if optimize and (not callable(_checkpoint) or not isinstance(_report, dict)):
        raise ValueError("A aplicação exige reserva persistente; use --optimize com --journal.")
    if type(apply_timeout) is not int or not 10 <= apply_timeout <= 180:
        raise ValueError("O prazo de aplicação deve estar entre 10 e 180 segundos.")
    if expected_sha256 is not None:
        if not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
            raise ValueError("A identidade esperada deve ter 64 caracteres hexadecimais.")
        expected_sha256 = expected_sha256.lower()
    if owner_email is not None:
        if expected_sha256 is None:
            raise ValueError("A consulta TPAP exige --expected-sha256.")
        if (not isinstance(owner_email, str) or len(owner_email.encode("utf-8")) > 254
                or "\0" in owner_email or not re.fullmatch(r"[^@\s]+@[^@\s]+", owner_email)):
            raise ValueError("Informe o e-mail da conta proprietária no Terminal.")
    if password is not None:
        if owner_email is None:
            raise ValueError("A autenticação exige o e-mail da conta e --expected-sha256.")
        if not isinstance(password, str) or not 1 <= len(password.encode("utf-8")) <= 256 or "\0" in password:
            raise ValueError("Informe uma senha não vazia de até 256 bytes, sem caractere nulo.")
        crypto_modules()  # Missing dependency must not consume a device login attempt.
    if (read_info or scan_only or optimize) and password is None:
        raise ValueError("A leitura/análise exige autenticação com senha e certificado fixado.")
    if (_guest_callback is not None or _guest_read) and (not read_info or scan_only or optimize):
        raise ValueError('Guest control requires authenticated read-info mode.')
    result = {
        "probe_version": 6,
        "state": "not_connected",
        "port": PORT,
        "vendor_chain_verified": False,
        "hostname_verified": False,
        "certificate_pin_matched": None,
        "authentication": "not_tested",
        "optimization": "not_tested",
        "application_bytes_sent": 0,
        "application_send_attempted": False,
        "application_messages_sent": 0,
    }
    if password is not None:
        result.update(password_proof_attempted=False, password_proof_sent=False,
                      server_proof_verified=False, account_credentials_verified=False,
                      account_role_verified=False, session_token_received=False)
    if scan_only or optimize:
        result.update(apply_command_sent=False, scan={
            "state": "not_requested", "request_attempted": False, "request_sent": False,
            "response_received": False, "fresh_scan_confirmed": False,
            "result_may_be_cached": True,
        })
    if optimize:
        _report.update(result)
        result = _report
    started = time.monotonic()
    try:
        if _before_connect:
            _before_connect()
        context = make_context()
        deadline = started + timeout
        with socket.create_connection((str(address), PORT), timeout=timeout) as raw:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            raw.settimeout(remaining)
            with context.wrap_socket(raw, server_hostname=str(address)) as tls:
                certificate = tls.getpeercert()
                fingerprint = hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest()
                common_names = [value for rdn in certificate.get("subject", ())
                                for key, value in rdn if key == "commonName"]
                model_matches = common_names == ["Deco X50(US)"]
                result.update(
                    state="tls_verified" if model_matches else "unexpected_certificate_model",
                    vendor_chain_verified=True,
                    model="Deco X50(US)" if model_matches else "unrecognized",
                    tls_version=tls.version(),
                    cipher=tls.cipher()[0],
                    certificate_sha256=fingerprint,
                )
                if expected_sha256 is not None:
                    matched = hmac.compare_digest(fingerprint, expected_sha256)
                    result["certificate_pin_matched"] = matched
                    if not matched:
                        result["state"] = "certificate_pin_mismatch"
                if owner_email is not None and result["state"] == "tls_verified":
                    user_random = secrets.token_bytes(32)
                    packet, sequence = registration_packet(owner_email, user_random)
                    remaining_timeout(tls, deadline)
                    result.update(application_send_attempted=True, application_bytes_sent=None,
                                  authentication="parameters_only")
                    tls.sendall(packet)
                    result.update(application_bytes_sent=len(packet), application_messages_sent=1)
                    message = receive_registration(tls, sequence, deadline)
                    result.update(summarize_parameters(message))
                    if password is not None:
                        # PAKE verifies account credentials, not owner/admin permissions.
                        result.pop("owner_account_verified", None)
                        if result["state"] == "pake_parameters_received":
                            shared_key = authenticate(tls, deadline, message["result"], owner_email,
                                                      password, user_random, result)
                            if shared_key is not None and _login_verified:
                                _login_verified()
                            if (read_info or scan_only or optimize) and shared_key is not None:
                                reader = read_information(tls, shared_key, timeout, result,
                                                          allow_scan=scan_only or optimize, include_guest=_guest_read)
                                if _guest_callback is not None and result['state'] == 'readonly_verified':
                                    reader.allow_guest = True
                                    try:
                                        result['guest_action'] = _guest_callback(reader)
                                    finally:
                                        reader.allow_guest = False
                                if scan_only or optimize:
                                    scan_information(reader, timeout, scan_timeout, result)
                                if optimize:
                                    apply_information(reader, apply_timeout, result, _checkpoint)
                # Each mode closes this session; application is never retried.
    except ProtocolError as error:
        result.update(state="protocol_error", protocol_error=str(error))
    except ssl.SSLCertVerificationError as error:
        result.update(state="certificate_untrusted", verify_code=error.verify_code)
    except (TimeoutError, socket.timeout):
        result["state"] = "timeout"
    except ConnectionRefusedError:
        result["state"] = "connection_refused"
    except ssl.SSLError:
        result["state"] = "tls_error"
    except OSError:
        result["state"] = "connection_error"
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


class ActionStorageError(Exception):
    pass


class ActionCancelled(Exception):
    pass


def atomic_json(path, document):
    """Sync the file and directory before allowing a network mutation."""
    temporary = None
    try:
        encoded = json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2).encode()
        if len(encoded) > 2 * 1024 * 1024:
            raise ValueError()
        fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except (OSError, ValueError, TypeError):
        raise ActionStorageError() from None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def action_store(path):
    path = Path(path)
    if not path.is_absolute():
        raise ActionStorageError()
    lock_fd = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "r+") as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise ActionStorageError()
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            document = {"version": 1, "actions": {}}
        else:
            with os.fdopen(fd, "r") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024 * 1024:
                    raise ActionStorageError()
                document = json.load(handle, object_pairs_hook=unique_object)
        if (not isinstance(document, dict) or document.get("version") != 1
                or not isinstance(document.get("actions"), dict) or len(document["actions"]) > 128):
            raise ActionStorageError()
        for request_id, entry in document["actions"].items():
            if (not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", request_id) or not isinstance(entry, dict)
                    or not isinstance(entry.get("target"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", entry["target"])):
                raise ActionStorageError()
            report = entry.get("report")
            if (not isinstance(report, dict) or report.get("request_id") != request_id
                    or type(report.get("apply_command_attempted")) is not bool):
                raise ActionStorageError()
            if any(key in report and not isinstance(report[key], dict)
                   for key in ("apply_ack", "verification")):
                raise ActionStorageError()
            if report["apply_command_attempted"]:
                at = report.get("apply_attempted_at")
                if type(at) not in (int, float) or not math.isfinite(at) or at < 0:
                    raise ActionStorageError()
                baseline = report.get("channels_before_apply")
                if not isinstance(baseline, dict) or baseline.get("state") != "available":
                    raise ActionStorageError()
                clean = project_read("channels", json.dumps({
                    "error_code": 0, "result": baseline.get("data")}).encode())
                if clean != baseline or clean["state"] != "available":
                    raise ActionStorageError()
        yield document["actions"], lambda: atomic_json(path, document)


def radio_parameters(snapshot):
    if snapshot.get("state") != "available":
        return {}
    return {(band, section, key): value
            for band, sections in snapshot["data"].items()
            for section, fields in sections.items() for key, value in fields.items()}


def verify_application(report, connection, timeout, verify_timeout, checkpoint, delay,
                       wait=None, should_stop=None):
    """New authenticated READ-ONLY sessions; no reference to a scan/apply reader."""
    verification = {"state": "waiting", "attempts": 0, "failures": 0,
                    "stable_samples": 0, "observations": [], "started_at": int(time.time())}
    report.update(state="verifying", stage="verification", verification=verification,
                  conclusion="verification_in_progress")
    checkpoint(report)
    def pause(seconds):
        if (should_stop and should_stop()) or (wait(seconds) if wait else time.sleep(seconds)):
            raise ActionCancelled()
    pause(delay)
    deadline = time.monotonic() + verify_timeout
    baseline = radio_parameters(report["channels_before_apply"])
    previous = None
    for _ in range(6):
        if should_stop and should_stop():
            raise ActionCancelled()
        remaining = deadline - time.monotonic()
        if remaining < 5:
            break
        # Login + ALLOC + three reads each use this per-stage bound.
        observed = probe(**connection, timeout=min(timeout, remaining / 5), read_info=True)
        verification["attempts"] += 1
        verification["observations"].append({"state": observed["state"], "at": int(time.time())})
        current = observed.get("reads", {}).get("channels", {"state": "unavailable"})
        parameters = radio_parameters(current)
        if (observed["state"] == "readonly_verified" and supported_target(observed)
                and baseline.keys() <= parameters.keys()):
            verification["last_available"] = current
            verification["last_available_at"] = int(time.time())
            verification["stable_samples"] = verification["stable_samples"] + 1 if parameters == previous else 1
            previous = parameters
            if verification["stable_samples"] >= 3:
                verification["state"] = "stable"
                break
        else:
            verification["failures"] += 1
            verification["stable_samples"] = 0
            previous = None
            # Never repeat rejected credentials, unexpected identities or corrupt protocol.
            if observed["state"] not in ("timeout", "connection_refused", "connection_error",
                                          "tls_error", "readonly_incomplete"):
                verification["state"] = "verification_blocked"
                break
        verification["state"] = "observing"
        checkpoint(report)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        pause(min(10, remaining))
    if verification["state"] not in ("stable", "verification_blocked"):
        verification["state"] = "incomplete"
    verification["finished_at"] = int(time.time())
    final = verification.get("last_available")
    if final:
        verification["channel_comparison"] = compare_channels(report["channels_before_apply"], final)
        after = radio_parameters(final)
        verification["radio_changes"] = [
            {"band": b, "section": s, "parameter": k, "before": baseline[b, s, k], "after": after[b, s, k]}
            for b, s, k in sorted(baseline.keys() & after.keys()) if baseline[b, s, k] != after[b, s, k]]
    ack = report.get("apply_ack", {}).get("state")
    if ack == "rejected":
        report.update(state="apply_rejected", conclusion="apply_rejected")
    elif verification["state"] != "stable":
        report.update(state="verification_incomplete", conclusion="verification_incomplete")
    else:
        changed = verification["channel_comparison"]["observed_channels_changed"]
        suffix = "with_channel_change" if changed else "without_observed_channel_change"
        report.update(state="apply_acknowledged" if ack == "accepted" else "apply_outcome_uncertain",
                      conclusion=("acknowledged_" if ack == "accepted" else "unconfirmed_") + suffix)
    report["stage"] = "complete"
    checkpoint(report)


def action_error(actions, target, request_id, now, verify_only=False):
    """Same admission for the CLI and Home Assistant; no network or mutation."""
    entry = actions.get(request_id)
    if entry and entry["target"] != target:
        return {"state": "target_mismatch"}
    if entry and not verify_only:
        return {"state": "already_requested", "report": entry["report"]}
    if verify_only:
        if not entry or not entry["report"]["apply_command_attempted"]:
            return {"state": "nothing_to_verify"}
        return None
    if len(actions) >= 128:
        return {"state": "action_history_full"}
    for previous in actions.values():
        prior = previous["report"]
        if previous["target"] != target or not prior["apply_command_attempted"]:
            continue
        if (prior.get("verification", {}).get("state") != "stable"
                or prior.get("apply_ack", {}).get("state") != "accepted"):
            return {"state": "previous_apply_unverified", "previous_request_id": prior["request_id"]}
        remaining = prior["apply_attempted_at"] + 600 - now
        if remaining > 0:
            return {"state": "apply_cooldown", "remaining_seconds": math.ceil(remaining)}
    return None


def run_action(host, expected_sha256, owner_email, password, *, request_id, journal,
               verify_only=False, timeout=20, scan_timeout=120, apply_timeout=60, verify_timeout=120,
               _before_connect=None, _login_verified=None, _progress=None, _wait=None, _should_stop=None):
    """Persisted one-shot application and recovery, shared by CLI and app."""
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", request_id):
        raise ValueError("Informe --request-id com 1 a 48 letras, números, hífen ou sublinhado.")
    address = ipaddress.ip_address(host)
    if address.is_unspecified or address.is_multicast or not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256 or ""):
        raise ValueError("A ação exige IP do principal e --expected-sha256 válido.")
    for value, low, high in ((timeout, 1, 30), (scan_timeout, 30, 180),
                             (apply_timeout, 10, 180), (verify_timeout, 30, 300)):
        if type(value) is not int or not low <= value <= high:
            raise ValueError("Prazo inválido para a ação.")
    target = hashlib.sha256((str(address) + "|" + expected_sha256.lower()).encode()).hexdigest()
    connection = dict(host=str(address), expected_sha256=expected_sha256,
                      owner_email=owner_email, password=password)
    if _before_connect:
        connection['_before_connect'] = _before_connect
    if _login_verified:
        connection['_login_verified'] = _login_verified
    report = {}
    action_started = time.monotonic()
    try:
        with action_store(journal) as (actions, save):
            error = action_error(actions, target, request_id, time.time(), verify_only)
            if error:
                return error
            entry = actions.get(request_id)
            if verify_only:
                report = entry["report"]
            else:
                report = {"request_id": request_id, "state": "preflight_reserved",
                          "started_at": int(time.time()), "apply_command_attempted": False}
                actions[request_id] = {"target": target, "report": report}

            def checkpoint(current):
                actions[request_id]["report"] = current
                save()
                if _progress:
                    _progress(current)

            def network_checkpoint(current):
                checkpoint(current)
                if _should_stop and _should_stop():
                    raise ActionCancelled()

            checkpoint(report)  # Consume the ID before any login or active scan.
            try:
                if _should_stop and _should_stop():
                    raise ActionCancelled()
                if not verify_only:
                    probe(**connection, timeout=timeout, scan_timeout=scan_timeout, optimize=True,
                          apply_timeout=apply_timeout, _checkpoint=network_checkpoint, _report=report)
                    if "elapsed_seconds" in report:
                        report["request_session_seconds"] = report.pop("elapsed_seconds")
                    checkpoint(report)
                if report["apply_command_attempted"]:
                    delay = max(0, 30 - (time.time() - report["apply_attempted_at"])) if verify_only else 30
                    verify_application(report, connection, timeout, verify_timeout, checkpoint, delay,
                                       wait=_wait, should_stop=_should_stop)
                if verify_only:
                    report["last_verified_at"] = int(time.time())
            except (KeyboardInterrupt, ActionCancelled):
                report.update(state="interrupted", conclusion="verification_incomplete")
            if verify_only:
                report["last_verification_seconds"] = round(time.monotonic() - action_started, 3)
            else:
                report["finished_at"] = int(time.time())
                report["elapsed_seconds"] = round(time.monotonic() - action_started, 3)
            checkpoint(report)
            return report
    except BlockingIOError:
        return {"state": "action_in_progress"}
    except (ActionStorageError, OSError, ValueError, TypeError, KeyError, ProtocolError):
        return {"state": "action_storage_or_state_error", "retry_automatically": False,
                "apply_may_have_been_attempted": report.get("apply_command_attempted")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="IP do X50 principal")
    parser.add_argument("--timeout", type=int, default=8)
    parser.add_argument("--expected-sha256", help="Identidade obtida em uma execução anterior")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--pake-parameters", action="store_true",
                        help="Consultar uma vez os parâmetros TPAP; não solicita senha")
    modes.add_argument("--authenticate", action="store_true",
                       help="Testar um login com senha oculta; não executa análise ou otimização")
    modes.add_argument("--read-info", action="store_true",
                       help="Login e leitura única de modo, canais e inventário; sem varredura")
    modes.add_argument("--scan-only", action="store_true",
                       help="Uma análise de interferência nos perfis X50 Router suportados; nunca aplica canais")
    modes.add_argument("--optimize", action="store_true",
                       help="Analisar e aplicar uma vez se recomendado; pode interromper o Wi-Fi")
    modes.add_argument("--verify-only", action="store_true",
                       help="Verificar tentativa registrada; não analisa nem reaplica")
    parser.add_argument("--request-id", help="Identificador único do pedido de aplicação/verificação")
    parser.add_argument("--journal", default="/config/ezmart_x50_apply_journal.json",
                        help="Registro persistente de tentativas; preserve-o entre execuções")
    parser.add_argument("--apply-timeout", type=int, default=60)
    parser.add_argument("--verify-timeout", type=int, default=120)
    parser.add_argument("--scan-timeout", type=int, default=120,
                        help="Prazo da análise: 30 a 180 segundos (padrão: 120)")
    args = parser.parse_args()
    owner_email = None
    password = None
    action = args.optimize or args.verify_only
    if action and not args.request_id:
        parser.error("A aplicação/verificação exige --request-id.")
    login = args.authenticate or args.read_info or args.scan_only or action
    if args.pake_parameters or login:
        if not args.expected_sha256:
            parser.error("A negociação/login exige --expected-sha256.")
        if login:
            try:
                crypto_modules()
            except ValueError as error:
                parser.error(str(error))
        if not sys.stdin.isatty():
            parser.error("Execute em um Terminal interativo para informar o e-mail localmente.")
        try:
            # getpass must not silently fall back to visible keyboard input.
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                owner_email = getpass.getpass("E-mail da conta proprietária TP-Link (entrada oculta): ").strip()
                if login:
                    password = getpass.getpass("Senha dessa conta TP-Link (entrada oculta): ")
        except getpass.GetPassWarning:
            parser.error("Este Terminal não permite entrada oculta; nenhuma conexão foi iniciada.")
        except (EOFError, KeyboardInterrupt):
            return 1
    try:
        options = {"read_info": True} if args.read_info else {}
        if args.scan_only:
            options = {"scan_only": True, "scan_timeout": args.scan_timeout}
        if action:
            result = run_action(args.host, args.expected_sha256, owner_email, password,
                                request_id=args.request_id, journal=args.journal, verify_only=args.verify_only,
                                timeout=args.timeout, scan_timeout=args.scan_timeout,
                                apply_timeout=args.apply_timeout, verify_timeout=args.verify_timeout)
        else:
            result = probe(args.host, args.timeout, args.expected_sha256,
                           owner_email=owner_email, password=password, **options)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if action:
        return 0 if (result.get("conclusion", "").startswith("acknowledged_")
                     and result.get("verification", {}).get("state") == "stable"
                     or result["state"] == "optimization_not_needed") else 1
    success = ("scan_response_received",) if args.scan_only else (
        "readonly_verified",) if args.read_info else ("authentication_verified",) if args.authenticate else (
        "pake_parameters_received",) if args.pake_parameters else ("tls_verified",)
    return 0 if result["state"] in success else 1


if __name__ == "__main__":
    raise SystemExit(main())
