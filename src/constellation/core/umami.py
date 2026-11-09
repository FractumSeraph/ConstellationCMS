"""Umami: read the tracking code people paste, and build what the site needs from it.

Umami's "Tracking code" looks like::

    <script defer src="https://cloud.umami.is/script.js" data-website-id="94db1cb1-..."></script>

From that one line the site gets everything: the script tag on every web page (limited to the
site's own hostnames, so previews and local builds are never counted) and the address the server
forwards Gemini and Gopher page views to (``<umami>/api/send``).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

_SCRIPT = re.compile(r"<script\b([^>]*)>", re.I | re.S)
_ATTR = re.compile(
    r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*(?:=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?"""
)
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class UmamiCodeError(ValueError):
    """The pasted text is not an Umami tracking code; the message says what to paste instead."""


@dataclass(frozen=True)
class UmamiCode:
    script_url: str
    website_id: str
    host_url: str = ""  # data-host-url, when Umami's API lives somewhere other than the script

    @property
    def api_base(self) -> str:
        if self.host_url:
            return self.host_url.rstrip("/")
        parts = urlsplit(self.script_url)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def send_endpoint(self) -> str:
        return self.api_base + "/api/send"


def _attrs(tag_inner: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _ATTR.finditer(tag_inner):
        out[m.group(1).lower()] = html.unescape(m.group(2) or m.group(3) or m.group(4) or "")
    return out


def _https_url(value: str, what: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in ("https", "http") or not parts.netloc:
        raise UmamiCodeError(f"The {what} must be a full address starting with https://.")
    if parts.scheme != "https":
        raise UmamiCodeError(
            f"The {what} uses http://. Browsers block it on an https site; use the https:// address."
        )
    return value.strip()


def parse_tracking_code(text: str) -> UmamiCode:
    """Read an Umami tracking code. Raises :class:`UmamiCodeError` with a plain explanation."""
    text = (text or "").strip()
    if not text:
        raise UmamiCodeError(
            "Paste the tracking code from Umami (Settings → Websites → Edit → Tracking code)."
        )
    for m in _SCRIPT.finditer(text):
        attrs = _attrs(m.group(1))
        website_id = attrs.get("data-website-id", "").strip()
        if not website_id:
            continue
        if not _UUID.match(website_id):
            raise UmamiCodeError(f"The website ID {website_id!r} doesn't look like one from Umami.")
        src = attrs.get("src", "")
        if not src:
            raise UmamiCodeError("The tracking code has no script address (src).")
        host_url = attrs.get("data-host-url", "")
        return UmamiCode(
            script_url=_https_url(src, "script address"),
            website_id=website_id.lower(),
            host_url=_https_url(host_url, "data-host-url address") if host_url else "",
        )
    if "<script" in text.lower():
        raise UmamiCodeError(
            "That script has no data-website-id, so it isn't an Umami tracking code. "
            "Copy it from Umami: Settings → Websites → Edit → Tracking code."
        )
    raise UmamiCodeError(
        "That isn't a tracking code. Paste the whole <script …></script> line from Umami "
        "(Settings → Websites → Edit → Tracking code)."
    )


def from_config(cfg: object) -> UmamiCode | None:
    """The site's Umami settings (``[analytics.umami]`` in site.toml), or None when not set."""
    u = getattr(getattr(cfg, "analytics", None), "umami", None)
    if u is None or not u.website_id or not u.script_url:
        return None
    return UmamiCode(script_url=u.script_url, website_id=u.website_id, host_url=u.host_url)


def forward_settings(cfg: object) -> dict[str, object]:
    """What the server needs to send Gemini and Gopher views to Umami.

    A hand-written ``[analytics.forward_to_umami]`` wins; otherwise it follows from the pasted
    tracking code unless forwarding was turned off.
    """
    analytics = getattr(cfg, "analytics", None)
    manual = getattr(analytics, "forward_to_umami", None)
    if manual is not None and manual.enabled:
        return dict(manual.model_dump())
    code = from_config(cfg)
    if code is not None and getattr(analytics, "umami").forward_other_platforms:  # noqa: B009
        return {"enabled": True, "endpoint": code.send_endpoint, "website_id": code.website_id}
    return {"enabled": False, "endpoint": "", "website_id": ""}


def script_tag(code: UmamiCode, domains: list[str]) -> str:
    """The tag for every web page. ``domains`` limits counting to the site's real hostnames."""
    attrs = [
        "defer",
        f'src="{html.escape(code.script_url, quote=True)}"',
        f'data-website-id="{html.escape(code.website_id, quote=True)}"',
    ]
    if code.host_url:
        attrs.append(f'data-host-url="{html.escape(code.host_url, quote=True)}"')
    clean = [d.strip().lower() for d in domains if d and d.strip()]
    if clean:
        attrs.append(f'data-domains="{html.escape(",".join(dict.fromkeys(clean)), quote=True)}"')
    return f"<script {' '.join(attrs)}></script>"


def strip_matching_snippet(head_html: str, website_id: str) -> tuple[str, bool]:
    """Remove a hand-pasted Umami script for the same website from custom HTML.

    Returns the new HTML and whether anything was removed, so the studio can move a snippet that
    used to live in Custom HTML into the Umami setting without loading the script twice.
    """
    pattern = re.compile(
        r"[ \t]*<script\b[^>]*data-website-id\s*=\s*[\"']?"
        + re.escape(website_id)
        + r"[\"']?[^>]*>\s*</script>[ \t]*\n?",
        re.I,
    )
    new, n = pattern.subn("", head_html or "")
    return new, n > 0
