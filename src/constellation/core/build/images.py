"""Responsive image variants for the web platform (Pillow)."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

RASTER = {".png", ".jpg", ".jpeg", ".webp"}
WIDTHS = (("sm", 480), ("md", 960), ("lg", 1600))


@dataclass(slots=True)
class Variant:
    path: str          # site-relative URL path of the variant
    data: bytes
    width: int
    media_type: str


@dataclass(slots=True)
class ImageSet:
    source_path: str   # site-relative URL path of the original
    width: int
    height: int
    variants: list[Variant] = field(default_factory=list)

    def srcset(self) -> str:
        parts = [f"{v.path} {v.width}w" for v in self.variants if not v.path.endswith(".webp")]
        if not parts:
            return ""
        parts.append(f"{self.source_path} {self.width}w")
        return ", ".join(parts)

    def srcset_webp(self) -> str:
        return ", ".join(f"{v.path} {v.width}w" for v in self.variants if v.path.endswith(".webp"))


def make_variants(source: Path, url_path: str) -> ImageSet | None:
    """Resized copies at the standard widths (only narrower than the original) plus WebP."""
    if source.suffix.lower() not in RASTER:
        return None
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        return None
    try:
        with Image.open(source) as im:
            im.load()
            width, height = im.size
            iset = ImageSet(url_path, width, height)
            stem, ext = url_path.rsplit(".", 1)
            fmt = "PNG" if ext.lower() == "png" else "WEBP" if ext.lower() == "webp" else "JPEG"
            base = im.convert("RGBA") if fmt == "PNG" else im.convert("RGB")
            for name, w in WIDTHS:
                if w >= width:
                    continue
                h = max(1, round(height * w / width))
                resized = base.resize((w, h), Image.Resampling.LANCZOS)
                iset.variants.append(Variant(f"{stem}-{name}.{ext}", _encode(resized, fmt), w, _mt(fmt)))
                iset.variants.append(Variant(f"{stem}-{name}.webp", _encode(resized, "WEBP"), w, "image/webp"))
            if fmt != "WEBP":
                iset.variants.append(Variant(f"{stem}.webp", _encode(base, "WEBP"), width, "image/webp"))
            return iset
    except Exception:
        return None


def _encode(im: object, fmt: str) -> bytes:
    buf = io.BytesIO()
    kwargs: dict[str, object] = {"optimize": True} if fmt in ("PNG", "JPEG") else {"method": 4, "quality": 82}
    if fmt == "JPEG":
        kwargs["quality"] = 85
        kwargs["progressive"] = True
    im.save(buf, fmt, **kwargs)  # type: ignore[attr-defined]
    return buf.getvalue()


def _mt(fmt: str) -> str:
    return {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}[fmt]
