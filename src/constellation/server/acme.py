"""Let's Encrypt (or any ACME v2 CA) certificates via HTTP-01, served by our own port 80.

The orchestration is separated from the wire client so it can be tested without a CA:
``Orchestrator`` decides which hostnames need certificates and when; ``AcmeClient`` (built on
certbot's ``acme`` library) talks to the CA; ``ChallengeStore`` hands the HTTP app the tokens.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from constellation.server.tls import cert_dir, expiry

log = logging.getLogger("constellation.server.acme")
RENEW_BEFORE_DAYS = 30


class ChallengeStore:
    """token → key authorization, served at /.well-known/acme-challenge/<token>."""

    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}
        self._lock = threading.Lock()

    def put(self, token: str, key_authorization: str) -> None:
        with self._lock:
            self._tokens[token] = key_authorization

    def pop(self, token: str) -> None:
        with self._lock:
            self._tokens.pop(token, None)

    def get(self, token: str) -> str | None:
        with self._lock:
            return self._tokens.get(token)


class CertificateIssuer(Protocol):
    def issue(self, hostnames: list[str], challenges: ChallengeStore) -> tuple[bytes, bytes]:
        """Return (fullchain PEM, private key PEM) for the hostnames, using HTTP-01."""
        ...


class AcmeClient:
    """A thin wrapper over certbot's ``acme`` library (ACME v2, HTTP-01)."""

    def __init__(
        self,
        directory_url: str,
        email: str,
        account_dir: Path,
        *,
        user_agent: str = "constellationcms",
    ) -> None:
        self.directory_url = directory_url
        self.email = email
        self.account_dir = Path(account_dir)
        self.user_agent = user_agent

    def _account_key(self) -> Any:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from josepy.jwk import JWKRSA

        self.account_dir.mkdir(parents=True, exist_ok=True)
        path = self.account_dir / "account.key"
        if path.is_file():
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        else:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            path.write_bytes(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            path.chmod(0o600)
        return JWKRSA(key=key)

    def issue(self, hostnames: list[str], challenges_store: ChallengeStore) -> tuple[bytes, bytes]:
        from acme import challenges, client, crypto_util, messages
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        key = self._account_key()
        net = client.ClientNetwork(key, user_agent=self.user_agent)
        directory = client.ClientV2.get_directory(self.directory_url, net)
        acme = client.ClientV2(directory, net=net)
        regr_path = self.account_dir / "registration.json"
        if regr_path.is_file():
            net.account = messages.RegistrationResource.json_loads(
                regr_path.read_text(encoding="utf-8")
            )
        else:
            regr = acme.new_account(
                messages.NewRegistration.from_data(
                    email=self.email or None, terms_of_service_agreed=True
                )
            )
            regr_path.write_text(regr.json_dumps(), encoding="utf-8")
        cert_key = ec.generate_private_key(ec.SECP256R1())
        key_pem = cert_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        csr_pem = crypto_util.make_csr(key_pem, hostnames)
        order = acme.new_order(csr_pem)
        tokens: list[str] = []
        try:
            for authz in order.authorizations:
                for challb in authz.body.challenges:
                    if isinstance(challb.chall, challenges.HTTP01):
                        response, validation = challb.response_and_validation(key)
                        token = challb.chall.encode("token")
                        challenges_store.put(token, validation)
                        tokens.append(token)
                        acme.answer_challenge(challb, response)
                        break
            order = acme.poll_and_finalize(
                order, deadline=dt.datetime.now() + dt.timedelta(seconds=120)
            )
        finally:
            for token in tokens:
                challenges_store.pop(token)
        return order.fullchain_pem.encode("utf-8"), key_pem


@dataclass(slots=True)
class HostStatus:
    hostname: str
    state: str = "missing"  # missing | valid | renewing | error
    expires: str | None = None
    error: str = ""
    checked: float = 0.0


@dataclass(slots=True)
class Orchestrator:
    data_dir: Path
    issuer: CertificateIssuer
    challenges: ChallengeStore
    hostnames: Callable[[], list[str]]
    on_change: Callable[[], None] | None = None
    status: dict[str, HostStatus] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def paths(self, host: str) -> tuple[Path, Path]:
        d = cert_dir(self.data_dir, "web", host)
        return d / "fullchain.pem", d / "privkey.pem"

    def has_valid(self, host: str, *, now: dt.datetime | None = None) -> bool:
        cert, key = self.paths(host)
        if not (cert.is_file() and key.is_file()):
            return False
        now = now or dt.datetime.now(dt.UTC)
        try:
            return expiry(cert) - now > dt.timedelta(days=RENEW_BEFORE_DAYS)
        except Exception:  # noqa: BLE001
            return False

    def ensure_all(self, *, now: dt.datetime | None = None) -> list[str]:
        """Issue or renew for every hostname that needs it; returns the hostnames changed."""
        changed: list[str] = []
        with self._lock:
            for host in sorted({h.lower() for h in self.hostnames()}):
                st = self.status.setdefault(host, HostStatus(host))
                st.checked = time.time()
                if self.has_valid(host, now=now):
                    st.state, st.error = "valid", ""
                    st.expires = expiry(self.paths(host)[0]).isoformat()
                    continue
                st.state = "renewing"
                try:
                    fullchain, key = self.issuer.issue([host], self.challenges)
                    cert_path, key_path = self.paths(host)
                    cert_path.parent.mkdir(parents=True, exist_ok=True)
                    tmp_key = key_path.with_suffix(".tmp")
                    tmp_key.write_bytes(key)
                    tmp_key.chmod(0o600)
                    tmp_key.replace(key_path)
                    tmp_cert = cert_path.with_suffix(".tmp")
                    tmp_cert.write_bytes(fullchain)
                    tmp_cert.replace(cert_path)
                    st.state, st.error = "valid", ""
                    st.expires = expiry(cert_path).isoformat()
                    changed.append(host)
                    log.info("certificate issued for %s", host)
                except Exception as e:  # noqa: BLE001
                    st.state, st.error = "error", str(e)[:300]
                    log.warning("certificate for %s failed: %s", host, e)
        if changed and self.on_change:
            self.on_change()
        self._save_status()
        return changed

    def _save_status(self) -> None:
        p = self.data_dir / "certs" / "acme-status.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({h: _as_dict(s) for h, s in self.status.items()}, indent=1, default=str),
            encoding="utf-8",
        )

    def report(self) -> list[dict[str, Any]]:
        return [_as_dict(s) for s in sorted(self.status.values(), key=lambda s: s.hostname)]


def _as_dict(s: HostStatus) -> dict[str, Any]:
    return {
        "hostname": s.hostname,
        "state": s.state,
        "expires": s.expires,
        "error": s.error,
        "checked": s.checked,
    }
