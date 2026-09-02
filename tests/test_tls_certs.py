"""
test_tls_certs.py - Certificate generation and TLS client wiring

Verifies the generated CA/prover/verifier certificates are actually
correctly formed and chained - not just that the generator ran without
raising - and that the verifier only attempts mutual TLS when a complete
set of TLS env vars is present.

    pytest tests/test_tls_certs.py -v
"""

import datetime

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.x509.oid import ExtendedKeyUsageOID

from scripts.gen_tls_certs import make_ca, make_leaf


@pytest.fixture(scope="module")
def ca():
    return make_ca()


@pytest.fixture(scope="module")
def prover_cert(ca):
    ca_key, ca_cert = ca
    return make_leaf(ca_key, ca_cert, "zeroaudit-prover",
                     sans=["prover", "localhost", "127.0.0.1"], client_auth=False)


@pytest.fixture(scope="module")
def verifier_cert(ca):
    ca_key, ca_cert = ca
    return make_leaf(ca_key, ca_cert, "zeroaudit-verifier", sans=None, client_auth=True)


def _verify_signed_by(leaf_cert, ca_cert):
    """Raises if leaf_cert's signature does not verify against ca_cert's key."""
    ca_cert.public_key().verify(
        leaf_cert.signature, leaf_cert.tbs_certificate_bytes,
        padding.PKCS1v15(), leaf_cert.signature_hash_algorithm,
    )


class TestCA:
    def test_is_a_ca_certificate(self, ca):
        _, cert = ca
        bc = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
        assert bc.ca is True

    def test_is_self_signed(self, ca):
        _, cert = ca
        _verify_signed_by(cert, cert)     # does not raise

    def test_has_key_identifiers(self, ca):
        _, cert = ca
        cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)
        cert.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier)

    def test_valid_now(self, ca):
        _, cert = ca
        now = datetime.datetime.now(datetime.timezone.utc)
        assert cert.not_valid_before_utc <= now <= cert.not_valid_after_utc


class TestProverCertificate:
    def test_signed_by_the_ca(self, ca, prover_cert):
        _, ca_cert = ca
        _, cert = prover_cert
        _verify_signed_by(cert, ca_cert)     # does not raise

    def test_is_not_a_ca(self, prover_cert):
        _, cert = prover_cert
        bc = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
        assert bc.ca is False

    def test_sans_include_the_docker_service_name(self, prover_cert):
        """
        The verifier connects to https://prover:8000 - if "prover" isn't a
        SAN, TLS hostname verification fails even with a CA-trusted cert.
        """
        _, cert = prover_cert
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        dns_names = san.get_values_for_type(x509.DNSName)
        assert "prover" in dns_names
        assert "localhost" in dns_names

    def test_has_server_auth_eku(self, prover_cert):
        _, cert = prover_cert
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        assert ExtendedKeyUsageOID.SERVER_AUTH in eku
        assert ExtendedKeyUsageOID.CLIENT_AUTH not in eku


class TestVerifierCertificate:
    def test_signed_by_the_ca(self, ca, verifier_cert):
        _, ca_cert = ca
        _, cert = verifier_cert
        _verify_signed_by(cert, ca_cert)     # does not raise

    def test_has_client_auth_eku(self, verifier_cert):
        _, cert = verifier_cert
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        assert ExtendedKeyUsageOID.CLIENT_AUTH in eku
        assert ExtendedKeyUsageOID.SERVER_AUTH not in eku


class TestCrossValidation:
    def test_a_second_ca_does_not_validate_the_first_cas_leaf(self, prover_cert):
        """A cert from a different CA must not verify against this one."""
        _, other_ca_cert = make_ca()
        _, cert = prover_cert
        with pytest.raises(Exception):
            _verify_signed_by(cert, other_ca_cert)


class TestVerifierClientWiring:
    """verifier.dashboard._prover_client_kwargs() - only attempts mTLS with
    a complete set of env vars, never a partial one that would silently
    connect without a client cert."""

    def test_no_env_vars_means_plain_http_kwargs(self, monkeypatch):
        for var in ("TLS_VERIFIER_CERT_FILE", "TLS_VERIFIER_KEY_FILE", "TLS_CA_FILE"):
            monkeypatch.delenv(var, raising=False)
        from verifier.dashboard import _prover_client_kwargs
        assert _prover_client_kwargs() == {}

    def test_complete_env_vars_produce_cert_and_verify_kwargs(self, monkeypatch):
        monkeypatch.setenv("TLS_VERIFIER_CERT_FILE", "/certs/verifier.crt")
        monkeypatch.setenv("TLS_VERIFIER_KEY_FILE", "/certs/verifier.key")
        monkeypatch.setenv("TLS_CA_FILE", "/certs/ca.crt")
        from verifier.dashboard import _prover_client_kwargs
        kwargs = _prover_client_kwargs()
        assert kwargs["cert"] == ("/certs/verifier.crt", "/certs/verifier.key")
        assert kwargs["verify"] == "/certs/ca.crt"

    @pytest.mark.parametrize("missing", [
        "TLS_VERIFIER_CERT_FILE", "TLS_VERIFIER_KEY_FILE", "TLS_CA_FILE",
    ])
    def test_partial_env_vars_fall_back_to_plain_http(self, monkeypatch, missing):
        """
        A half-configured environment must not silently connect without a
        client cert while believing it's protected - it should fall all the
        way back to the same plain-HTTP mode as fully unconfigured.
        """
        env = {"TLS_VERIFIER_CERT_FILE": "/certs/verifier.crt",
              "TLS_VERIFIER_KEY_FILE": "/certs/verifier.key",
              "TLS_CA_FILE": "/certs/ca.crt"}
        del env[missing]
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        monkeypatch.delenv(missing, raising=False)
        from verifier.dashboard import _prover_client_kwargs
        assert _prover_client_kwargs() == {}
