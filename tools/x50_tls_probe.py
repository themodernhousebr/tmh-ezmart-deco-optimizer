#!/usr/bin/env python3
"""Standalone X50 TLS probe with optional TPAP login-parameter negotiation.

No password proof, authenticated session, TMP channel or ACS calls.

Python 3.9+ standard library only. The public CA comes from the official
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
import zlib


PORT = 30001
HEADER = struct.Struct(">BBBBI8s4sI")
CRC_SEED = 0x92BF30ED
MAX_BODY = 8192
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


def registration_packet(owner_email):
    """Firmware prover_SendParamRequest: one existing-account negotiation."""
    sequence = secrets.token_bytes(4)
    request = {"method": "login", "params": {
        "sub_method": "pake_register",
        "username": hashlib.sha256(owner_email.encode("utf-8")).hexdigest(),
        "user_random": base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
        "passcode_type": "userpw",
        "cipher_suites": [1],
        "encryption": ["aes_128_ccm"],
    }}
    body = json.dumps(request, separators=(",", ":")).encode("utf-8")
    packet = HEADER.pack(1, 1, 1, 0, len(body), b"\0" * 8, sequence, CRC_SEED) + body
    return packet[:20] + struct.pack(">I", zlib.crc32(packet)) + body, sequence


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


def summarize_parameters(message):
    """Do not export salts, points, nonces, account hashes, tokens or raw JSON."""
    if not isinstance(message, dict) or type(message.get("error_code")) is not int:
        raise ProtocolError("invalid_response_shape")
    code = message["error_code"]
    if not -(2**31) <= code < 2**31:
        raise ProtocolError("invalid_api_code")
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
        value = params.get(name)
        try:
            data = base64.b64decode(value, validate=True) if isinstance(value, str) else b""
        except (ValueError, binascii.Error):
            raise ProtocolError("invalid_parameter_encoding") from None
        if not minimum <= len(data) <= maximum:
            raise ProtocolError("invalid_parameter_length")
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


def probe(host, timeout=8, expected_sha256=None, owner_email=None):
    # Literal unicast IPs avoid DNS delays and accidental broadcast discovery.
    address = ipaddress.ip_address(host)
    if address.is_unspecified or address.is_multicast:
        raise ValueError("Use o IP do Deco principal.")
    if not 1 <= timeout <= 30:
        raise ValueError("O prazo deve estar entre 1 e 30 segundos.")
    if expected_sha256 is not None:
        if not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
            raise ValueError("A identidade esperada deve ter 64 caracteres hexadecimais.")
        expected_sha256 = expected_sha256.lower()
    if owner_email is not None:
        if expected_sha256 is None:
            raise ValueError("A consulta TPAP exige --expected-sha256.")
        if (not isinstance(owner_email, str) or len(owner_email.encode("utf-8")) > 254
                or not re.fullmatch(r"[^@\s]+@[^@\s]+", owner_email)):
            raise ValueError("Informe o e-mail da conta proprietária no Terminal.")
    result = {
        "probe_version": 2,
        "state": "not_connected",
        "port": PORT,
        "vendor_chain_verified": False,
        "hostname_verified": False,
        "certificate_pin_matched": None,
        "authentication": "not_tested",
        "optimization": "not_tested",
        "application_bytes_sent": 0,
        "application_send_attempted": False,
    }
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
                    packet, sequence = registration_packet(owner_email)
                    remaining_timeout(tls, deadline)
                    result.update(application_send_attempted=True, application_bytes_sent=None,
                                  authentication="parameters_only")
                    tls.sendall(packet)
                    result["application_bytes_sent"] = len(packet)
                    message = receive_registration(tls, sequence, deadline)
                    result.update(summarize_parameters(message))
                # Close without password proof or an authenticated TMP channel.
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
    parser.add_argument("--pake-parameters", action="store_true",
                        help="Consultar uma vez os parâmetros TPAP; não solicita senha")
    args = parser.parse_args()
    owner_email = None
    if args.pake_parameters:
        if not args.expected_sha256:
            parser.error("--pake-parameters exige --expected-sha256.")
        if not sys.stdin.isatty():
            parser.error("Execute em um Terminal interativo para informar o e-mail localmente.")
        try:
            owner_email = getpass.getpass("E-mail da conta proprietária TP-Link (entrada oculta): ").strip()
        except (EOFError, KeyboardInterrupt):
            return 1
    try:
        result = probe(args.host, args.timeout, args.expected_sha256, owner_email=owner_email)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["state"] in ("tls_verified", "pake_parameters_received") else 1


if __name__ == "__main__":
    raise SystemExit(main())
