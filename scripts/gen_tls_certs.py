"""
gen_tls_certs.py - Generate a local CA and mutual-TLS certificates for the
prover<->verifier link.

    python -m scripts.gen_tls_certs

Replaces "the verifier fetches the prover's public key over plain HTTP at
boot and trusts it on first use" with real mutual TLS: the prover serves
/keys only over HTTPS and requires the verifier to present a client
certificate signed by the same CA before it will even complete the TLS
handshake. A network position that can intercept the old plaintext GET
can no longer intercept anything (TLS), and cannot impersonate the
verifier to the prover either (client cert required).

What this does and does not upgrade to
---------------------------------------
This is real X.509 mutual authentication over a real TLS handshake - not
a simulation of it. It is NOT SGX remote attestation: it proves "this
peer holds a private key signed by our CA," not "this peer is running
inside an untampered enclave measured against a known-good hash." That
distinction is worth keeping straight - mTLS closes the plaintext-and
anyone-can-ask gap; it doesn't manufacture hardware attestation that
doesn't exist here.

Output (all under ./certs, gitignored - regenerate per environment,
exactly like .env):
    ca.crt              root CA certificate (public - safe to distribute)
    ca.key              root CA private key  (keep it secret)
    prover.crt/.key     server certificate, SAN=prover,localhost,127.0.0.1
    verifier.crt/.key   client certificate, CN=zeroaudit-verifier
"""

import datetime
import ipaddress
import os

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "certs")
VALID_DAYS = 825   # under the 825-day cap several TLS stacks enforce for leaf certs


def _new_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _write(path: str, data: bytes):
    with open(path, "wb") as fh:
        fh.write(data)


def _key_pem(key) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def make_ca():
    key = _new_key()
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "ZEROAUDIT Demo Root CA"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ZEROAUDIT"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=VALID_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False, data_encipherment=False,
                key_agreement=False, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        # Some TLS stacks warn or refuse a chain where the CA lacks these -
        # cheap to include, avoids exactly that class of interop complaint.
        .add_extension(ski, critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ski),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return key, cert


def make_leaf(ca_key, ca_cert, common_name: str, sans=None, client_auth=False):
    key = _new_key()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.timezone.utc)

    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=VALID_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage(
                [x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH] if client_auth
                else [x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]
            ),
            critical=False,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
    )
    if sans:
        entries = []
        for s in sans:
            try:
                entries.append(x509.IPAddress(ipaddress.ip_address(s)))
            except ValueError:
                entries.append(x509.DNSName(s))
        builder = builder.add_extension(x509.SubjectAlternativeName(entries), critical=False)

    cert = builder.sign(ca_key, hashes.SHA256())
    return key, cert


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    print("generating root CA ...")
    ca_key, ca_cert = make_ca()
    _write(os.path.join(OUT_DIR, "ca.key"), _key_pem(ca_key))
    _write(os.path.join(OUT_DIR, "ca.crt"), ca_cert.public_bytes(serialization.Encoding.PEM))

    print("generating prover server certificate (SAN: prover, localhost, 127.0.0.1) ...")
    prover_key, prover_cert = make_leaf(
        ca_key, ca_cert, "zeroaudit-prover",
        sans=["prover", "localhost", "127.0.0.1"], client_auth=False,
    )
    _write(os.path.join(OUT_DIR, "prover.key"), _key_pem(prover_key))
    _write(os.path.join(OUT_DIR, "prover.crt"), prover_cert.public_bytes(serialization.Encoding.PEM))

    print("generating verifier client certificate ...")
    verifier_key, verifier_cert = make_leaf(
        ca_key, ca_cert, "zeroaudit-verifier", sans=None, client_auth=True,
    )
    _write(os.path.join(OUT_DIR, "verifier.key"), _key_pem(verifier_key))
    _write(os.path.join(OUT_DIR, "verifier.crt"), verifier_cert.public_bytes(serialization.Encoding.PEM))

    print("done - wrote ca.{crt,key}, prover.{crt,key}, verifier.{crt,key} to %s" % OUT_DIR)
    print("these are demo credentials - regenerate per environment, never commit them")


if __name__ == "__main__":
    main()
