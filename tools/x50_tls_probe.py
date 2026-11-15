#!/usr/bin/env python3
"""Standalone X50 TLS identity probe. No login, TPAP messages or ACS calls.

Python 3.9+ standard library only. The public CA comes from the official
X50 1.8.4 firmware documented in RESEARCH.md. It is scoped to this connection;
it is never installed in the operating system's trust store.
"""

import argparse
import hashlib
import hmac
import ipaddress
import json
import re
import socket
import ssl
import time


PORT = 30001
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


def probe(host, timeout=8, expected_sha256=None):
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
    result = {
        "probe_version": 1,
        "state": "not_connected",
        "port": PORT,
        "vendor_chain_verified": False,
        "hostname_verified": False,
        "certificate_pin_matched": None,
        "authentication": "not_tested",
        "optimization": "not_tested",
        "application_bytes_sent": 0,
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
                # Close immediately: no credentials, HTTP, discovery, or TMP.
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
    args = parser.parse_args()
    try:
        result = probe(args.host, args.timeout, args.expected_sha256)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["state"] == "tls_verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
