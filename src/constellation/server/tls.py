"""Certificates: self-signed per-hostname certificates for Gemini (and for HTTPS when nothing
better exists), stored as PEM under ``<data>/certs/<platform>/<hostname>/``."""

from __future__ import annotations

import datetime as dt
import hashlib
import ssl
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def cert_dir(data: Path, platform: str, hostname: str) -> Path:
    return Path(data) / "certs" / platform / hostname.lower()


def ensure_self_signed(
    data: Path, platform: str, hostname: str, *, years: int = 5
) -> tuple[Path, Path]:
    """Create key.pem and cert.pem for a hostname if they do not exist; return their paths."""
    d = cert_dir(data, platform, hostname)
    key_path, cert_path = d / "key.pem", d / "cert.pem"
    if key_path.is_file() and cert_path.is_file():
        return key_path, cert_path
    d.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=365 * years))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    tmp_key = key_path.with_suffix(".tmp")
    tmp_key.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    tmp_key.chmod(0o600)
    tmp_key.replace(key_path)
    tmp_cert = cert_path.with_suffix(".tmp")
    tmp_cert.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    tmp_cert.replace(cert_path)
    return key_path, cert_path


def regenerate(data: Path, platform: str, hostname: str, *, years: int = 5) -> tuple[Path, Path]:
    d = cert_dir(data, platform, hostname)
    for name in ("key.pem", "cert.pem"):
        (d / name).unlink(missing_ok=True)
    return ensure_self_signed(data, platform, hostname, years=years)


def fingerprint(cert_path: Path) -> str:
    cert = x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
    digest = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2)).upper()


def expiry(cert_path: Path) -> dt.datetime:
    cert = x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
    return cert.not_valid_after_utc


def server_context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(cert_path), str(key_path))
    return ctx


class SniTable:
    """hostname → SSLContext, swapped as a whole on reload; the default serves unknown names."""

    def __init__(self, default: ssl.SSLContext) -> None:
        self.default = default
        self.contexts: dict[str, ssl.SSLContext] = {}
        self.default.sni_callback = self._sni

    def set(self, contexts: dict[str, ssl.SSLContext]) -> None:
        self.contexts = {k.lower(): v for k, v in contexts.items()}

    def _sni(self, sock: ssl.SSLObject, server_name: str | None, _ctx: ssl.SSLContext) -> None:
        if server_name:
            ctx = self.contexts.get(server_name.lower())
            if ctx is not None:
                sock.context = ctx
        return None
