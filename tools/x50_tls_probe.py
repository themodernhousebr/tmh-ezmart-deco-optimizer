#!/usr/bin/env python3
"""Standalone X50 TLS, authentication, TMP reads and optional interference scan.

Only --scan-only requests an interference analysis, once. No mode applies channels.

Python 3.9+; authenticated modes require PyCryptodome. The public CA comes from the official
X50 1.8.4 firmware documented in RESEARCH.md. It is scoped to this connection;
it is never installed in the operating system's trust store.
"""

import argparse
import base64
import binascii
import getpass
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import socket
import ssl
import struct
import sys
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
    """Fixed reads and, when enabled, one ACS analysis; never channel application."""

    def __init__(self, channel, allow_scan=False):
        self.channel = channel
        self.sequence = 0
        self.transaction = -1
        self.allow_scan = allow_scan
        self.scan_transaction = None

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

    def request(self, opcode, timeout):
        self.channel.deadline = time.monotonic() + timeout
        self.transaction += 1
        body = b"" if opcode == 1 else json.dumps({
            "config_version": int(time.time() * 1000), "params": {}
        }, separators=(",", ":")).encode()
        self.send(opcode, body)
        result = bytearray()
        expected_total = None
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
            if (version, subversion) != (1, 2) or zlib.crc32(chunk) != crc:
                raise ProtocolError("tmp_business_invalid")
            if (returned, kind, tx) != (opcode, 5, self.transaction):
                raise ProtocolError("tmp_response_mismatch")
            if error:
                self.channel.report["tmp_error_code"] = error
                raise ProtocolError("tmp_request_rejected")
            if (total > MAX_TMP_RESULT or offset != len(result)
                    or (expected_total is not None and total != expected_total)):
                raise ProtocolError("tmp_fragment_invalid")
            expected_total = total
            result.extend(chunk)
            if len(result) > MAX_TMP_RESULT or (total and len(result) > total):
                raise ProtocolError("tmp_result_too_large")
            if total == 0 or len(result) == total:
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


def read_information(tls, shared_key, timeout, result, allow_scan=False):
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
        result["reads"][name] = project_read(name, reader.request(opcode, timeout))
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


def scan_information(reader, timeout, scan_timeout, result):
    """One explicitly selected ACS analysis on the physically identified profile."""
    result["stage"] = "scan_preflight"
    if result["state"] != "readonly_verified":
        result.update(state="scan_preflight_failed")
        return
    reads = result["reads"]
    masters = [node for node in reads["devices"]["data"]["nodes"] if node.get("role") == "master"]
    if (reads["mode"]["data"].get("mode") != "Router" or len(masters) != 1
            or masters[0].get("model") != "X50" or masters[0].get("hardware") != "1.30"
            or masters[0].get("firmware") != "1.8.4 Build 26051413 Rel. 27460"):
        result.update(state="scan_target_unsupported")
        return
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


def probe(host, timeout=8, expected_sha256=None, owner_email=None, password=None,
          read_info=False, scan_only=False, scan_timeout=120):
    # Literal unicast IPs avoid DNS delays and accidental broadcast discovery.
    address = ipaddress.ip_address(host)
    if address.is_unspecified or address.is_multicast:
        raise ValueError("Use o IP do Deco principal.")
    if not 1 <= timeout <= 30:
        raise ValueError("O prazo deve estar entre 1 e 30 segundos.")
    if type(scan_timeout) is not int or not 30 <= scan_timeout <= 180:
        raise ValueError("O prazo da análise deve estar entre 30 e 180 segundos.")
    if read_info and scan_only:
        raise ValueError("Selecione somente --read-info ou --scan-only.")
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
    if (read_info or scan_only) and password is None:
        raise ValueError("A leitura/análise exige autenticação com senha e certificado fixado.")
    result = {
        "probe_version": 5,
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
    if scan_only:
        result.update(apply_command_sent=False, scan={
            "state": "not_requested", "request_attempted": False, "request_sent": False,
            "response_received": False, "fresh_scan_confirmed": False,
            "result_may_be_cached": True,
        })
    started = time.monotonic()
    try:
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
                            if (read_info or scan_only) and shared_key is not None:
                                reader = read_information(tls, shared_key, timeout, result, allow_scan=scan_only)
                                if scan_only:
                                    scan_information(reader, timeout, scan_timeout, result)
                # Each mode closes this session; no retries or channel application.
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
                       help="Uma análise de interferência no X50 1.30/1.8.4 roteador; nunca aplica canais")
    parser.add_argument("--scan-timeout", type=int, default=120,
                        help="Prazo da análise: 30 a 180 segundos (padrão: 120)")
    args = parser.parse_args()
    owner_email = None
    password = None
    login = args.authenticate or args.read_info or args.scan_only
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
        result = probe(args.host, args.timeout, args.expected_sha256,
                       owner_email=owner_email, password=password, **options)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    success = ("scan_response_received",) if args.scan_only else (
        "readonly_verified",) if args.read_info else ("authentication_verified",) if args.authenticate else (
        "pake_parameters_received",) if args.pake_parameters else ("tls_verified",)
    return 0 if result["state"] in success else 1


if __name__ == "__main__":
    raise SystemExit(main())
