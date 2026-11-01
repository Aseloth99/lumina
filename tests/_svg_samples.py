"""Sentetik SVG denetim kümesi (internetten indirilmez, kişisel dosya yok): her biri belirli bir özelliği sınar."""

from __future__ import annotations

import base64
import gzip
import struct
import zlib

NS = 'xmlns="http://www.w3.org/2000/svg"'
XL = 'xmlns:xlink="http://www.w3.org/1999/xlink"'


def _png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


EMBEDDED_PNG = base64.b64encode(_png(8, 8, (200, 40, 40))).decode("ascii")

# ad -> SVG metni (str) ya da ham bayt; dosya adı uzantısı SAMPLE_EXT'ten ('.svg' varsayılan)
TEXT: dict[str, str] = {
    "clip_path": (
        f'<svg {NS} width="200" height="200" viewBox="0 0 200 200"><defs><clipPath id="c"><circle cx="100" cy="100" r="60"/>'
        '</clipPath></defs><rect width="200" height="200" fill="#d03030" clip-path="url(#c)"/>'
        '<circle cx="100" cy="100" r="60" fill="none" stroke="#202020" stroke-width="0.6"/></svg>'
    ),
    "mask": (
        f'<svg {NS} width="200" height="200" viewBox="0 0 200 200"><defs><linearGradient id="g"><stop offset="0" stop-color="#000"/>'
        '<stop offset="1" stop-color="#fff"/></linearGradient><mask id="m"><rect width="200" height="200" fill="url(#g)"/></mask></defs>'
        '<rect width="200" height="200" fill="#2060c0" mask="url(#m)"/></svg>'
    ),
    "gradients": (
        f'<svg {NS} width="240" height="120" viewBox="0 0 240 120"><defs><linearGradient id="l" x1="0" x2="1"><stop offset="0" stop-color="#f00"/>'
        '<stop offset="1" stop-color="#00f"/></linearGradient><radialGradient id="r"><stop offset="0" stop-color="#ff0"/>'
        '<stop offset="1" stop-color="#080"/></radialGradient></defs><rect width="120" height="120" fill="url(#l)"/>'
        '<circle cx="180" cy="60" r="55" fill="url(#r)"/></svg>'
    ),
    "filters": (
        f'<svg {NS} width="200" height="200" viewBox="0 0 200 200"><defs><filter id="b" x="-20%" y="-20%" width="140%" height="140%">'
        '<feGaussianBlur stdDeviation="4"/></filter><filter id="s" x="-30%" y="-30%" width="160%" height="160%">'
        '<feDropShadow dx="6" dy="6" stdDeviation="3" flood-color="#000" flood-opacity="0.6"/></filter></defs>'
        '<rect x="10" y="10" width="80" height="80" fill="#2a2" filter="url(#b)"/>'
        '<rect x="110" y="110" width="70" height="70" fill="#e82" filter="url(#s)"/></svg>'
    ),
    "text": (
        f'<svg {NS} width="300" height="120" viewBox="0 0 300 120"><rect width="300" height="120" fill="#fff"/>'
        '<text x="12" y="52" font-family="Arial, sans-serif" font-size="34" fill="#102040">Lumina SVG</text>'
        '<text x="12" y="96" font-family="Arial, sans-serif" font-size="18" fill="#a02020">Türkçe: ığüşöç İĞÜŞÖÇ</text></svg>'
    ),
    "use_symbol": (
        f'<svg {NS} {XL} width="240" height="120" viewBox="0 0 240 120"><defs><symbol id="s" viewBox="0 0 20 20">'
        '<circle cx="10" cy="10" r="8" fill="#c0392b"/><path d="M4 10h12" stroke="#fff" stroke-width="2"/></symbol>'
        '<g id="g"><rect width="30" height="30" fill="#27ae60"/></g></defs>'
        '<use xlink:href="#s" x="10" y="10" width="60" height="60"/><use href="#s" x="90" y="10" width="30" height="30"/>'
        '<use xlink:href="#g" x="150" y="20"/><use href="#g" x="190" y="60" transform="rotate(20 205 75)"/></svg>'
    ),
    "embedded_png": (
        f'<svg {NS} {XL} width="120" height="120" viewBox="0 0 120 120"><image x="10" y="10" width="100" height="100" '
        f'xlink:href="data:image/png;base64,{EMBEDDED_PNG}"/></svg>'
    ),
    "css_style": (
        f'<svg {NS} width="160" height="100" viewBox="0 0 160 100"><style>.a{{fill:#8e44ad}} #b{{fill:#16a085;stroke:#222;stroke-width:2}}'
        ' rect:nth-child(3){fill:#f39c12}</style><rect class="a" x="5" y="5" width="60" height="40"/>'
        '<circle id="b" cx="110" cy="30" r="22"/><rect x="5" y="55" width="60" height="40"/></svg>'
    ),
    "current_color": (
        f'<svg {NS} width="100" height="100" viewBox="0 0 100 100" color="#e74c3c"><circle cx="50" cy="50" r="30" fill="currentColor"/>'
        '<rect x="10" y="10" width="20" height="20" fill="none" stroke="currentColor" stroke-width="3"/></svg>'
    ),
    "transform_chain": (
        f'<svg {NS} width="200" height="200" viewBox="0 0 200 200"><g transform="translate(100 100) rotate(30) scale(1.2 0.8)">'
        '<g transform="skewX(15) translate(-30 -20)"><rect width="60" height="40" fill="#3498db" stroke="#000" stroke-width="1"/></g></g>'
        '<g transform="matrix(0.8 0.3 -0.3 0.8 20 20)"><circle r="10" fill="#e67e22"/></g></svg>'
    ),
    "pattern": (
        f'<svg {NS} width="200" height="120" viewBox="0 0 200 120"><defs><pattern id="p" width="12" height="12" patternUnits="userSpaceOnUse">'
        '<rect width="6" height="6" fill="#444"/><rect x="6" y="6" width="6" height="6" fill="#bbb"/></pattern></defs>'
        '<rect width="200" height="120" fill="url(#p)"/></svg>'
    ),
    "tiny_icon_16": (
        f'<svg {NS} width="16" height="16" viewBox="0 0 16 16"><path d="M2 8l4 4 8-8" fill="none" stroke="#1a1a1a" stroke-width="1.5"/></svg>'
    ),
    "huge_viewbox": (
        f'<svg {NS} viewBox="0 0 100000 100000"><rect x="10000" y="10000" width="80000" height="80000" fill="#1abc9c"/>'
        '<circle cx="50000" cy="50000" r="20000" fill="none" stroke="#c0392b" stroke-width="500"/></svg>'
    ),
    "viewbox_only": (
        f'<svg {NS} viewBox="0 0 300 150"><rect x="10" y="10" width="280" height="130" fill="none" stroke="#333" stroke-width="2"/>'
        '<circle cx="150" cy="75" r="50" fill="#c00000"/></svg>'
    ),
    "size_only": (
        f'<svg {NS} width="240" height="120"><rect x="10" y="10" width="220" height="100" fill="#ddd" stroke="#333"/>'
        '<circle cx="120" cy="60" r="40" fill="#2980b9"/></svg>'
    ),
    "percent_units": (
        f'<svg {NS} width="50%" height="100%" viewBox="0 0 300 150"><rect x="5" y="5" width="290" height="140" fill="none" stroke="#333"/>'
        '<circle cx="150" cy="75" r="40" fill="#8e44ad"/></svg>'
    ),
    "mm_units": (
        f'<svg {NS} width="60mm" height="40mm" viewBox="0 0 60 40"><rect x="1" y="1" width="58" height="38" fill="none" stroke="#000" stroke-width="0.4"/>'
        '<circle cx="30" cy="20" r="12" fill="#e74c3c"/></svg>'
    ),
    "pt_units": (
        f'<svg {NS} width="144pt" height="72pt"><rect width="144" height="72" fill="#f1c40f"/><circle cx="72" cy="36" r="30" fill="#2c3e50"/></svg>'
    ),
    "in_units": (
        f'<svg {NS} width="2in" height="1in" viewBox="0 0 200 100"><rect width="200" height="100" fill="#ecf0f1"/>'
        '<path d="M10 90 L100 10 L190 90 Z" fill="#16a085"/></svg>'
    ),
    "percent_content": (
        f'<svg {NS} width="200" height="100" viewBox="0 0 200 100"><rect width="100%" height="100%" fill="#ffeaa7"/>'
        '<rect x="10%" y="20%" width="30%" height="40%" fill="#d63031"/><circle cx="75%" cy="50%" r="20%" fill="#0984e3"/></svg>'
    ),
    "aspect_slice": (
        f'<svg {NS} width="200" height="100" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid slice"><rect width="100" height="100" fill="#74b9ff"/>'
        '<circle cx="50" cy="50" r="40" fill="#d63031"/></svg>'
    ),
    "aspect_meet_min": (
        f'<svg {NS} width="200" height="100" viewBox="0 0 100 100" preserveAspectRatio="xMinYMax meet"><rect width="100" height="100" fill="#55efc4"/>'
        '<circle cx="50" cy="50" r="40" fill="#6c5ce7"/></svg>'
    ),
    "aspect_none": (
        f'<svg {NS} width="200" height="100" viewBox="0 0 100 100" preserveAspectRatio="none"><rect width="100" height="100" fill="#fab1a0"/>'
        '<circle cx="50" cy="50" r="40" fill="#0984e3"/></svg>'
    ),
    "nested_svg": (
        f'<svg {NS} width="200" height="120" viewBox="0 0 200 120"><rect width="200" height="120" fill="#dfe6e9"/>'
        '<svg x="20" y="10" width="80" height="80" viewBox="0 0 10 10"><circle cx="5" cy="5" r="5" fill="#e17055"/></svg></svg>'
    ),
    "doctype_entity": (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!-- yorum <svg> -->\n<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
        '"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd" [<!ENTITY ns_svg "http://www.w3.org/2000/svg"><!ENTITY fillc "#e84393">]>\n'
        '<svg xmlns="&ns_svg;" width="120" height="120" viewBox="0 0 120 120"><circle cx="60" cy="60" r="50" fill="&fillc;"/></svg>'
    ),
    "root_attrs": (
        f'<svg {NS} x="40" y="40" width="150" height="90" viewBox="0 0 150 90" overflow="visible" style="background:#fff" '
        'fill="#00b894" stroke="#2d3436" stroke-width="3"><rect x="10" y="10" width="130" height="70"/></svg>'
    ),
    "opaque_bg": (
        f'<svg {NS} width="120" height="80" viewBox="0 0 120 80"><rect width="120" height="80" fill="#1e3a5f"/>'
        '<circle cx="60" cy="40" r="25" fill="#101010"/></svg>'
    ),
    "black_on_transparent": (
        f'<svg {NS} width="120" height="80" viewBox="0 0 120 80"><path d="M10 70 L60 10 L110 70 Z" fill="none" stroke="#000" stroke-width="3"/></svg>'
    ),
    "white_on_transparent": (
        f'<svg {NS} width="120" height="80" viewBox="0 0 120 80"><path d="M10 70 L60 10 L110 70 Z" fill="none" stroke="#fff" stroke-width="3"/></svg>'
    ),
    "thin_lines": (
        f'<svg {NS} width="300" height="200" viewBox="0 0 300 200"><rect width="300" height="200" fill="#fff"/>'
        '<g stroke="#000" stroke-width="0.25" fill="none"><path d="M0 0 L300 200 M0 200 L300 0"/>'
        '<circle cx="150" cy="100" r="60"/><circle cx="150" cy="100" r="30"/><path d="M20 20 H280 M20 180 H280 M20 20 V180 M280 20 V180"/></g>'
        '<text x="110" y="104" font-family="Arial" font-size="12" fill="#000">Lumina 0123</text></svg>'
    ),
    "relative_image": (
        f'<svg {NS} {XL} width="60" height="60" viewBox="0 0 60 60"><rect width="60" height="60" fill="#fff"/>'
        '<image x="10" y="10" width="40" height="40" xlink:href="red8.png"/></svg>'
    ),
    "empty_svg": f'<svg {NS} width="50" height="50"/>',
}

BROKEN: dict[str, bytes] = {
    "broken_unclosed": b"<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'><rect",
    "zero_bytes": b"",
    "not_svg_html": b"<html><body>hello</body></html>",
    "garbage": bytes(range(256)) * 4,
    "zero_size": f'<svg {NS} width="0" height="0"/>'.encode(),
}

ENCODED: dict[str, bytes] = {
    "utf16_bom": ("﻿" + TEXT["size_only"]).encode("utf-16"),
    "latin1_decl": (
        '<?xml version="1.0" encoding="ISO-8859-1"?>' + TEXT["text"].replace("Türkçe: ığüşöç İĞÜŞÖÇ", "Café ñ ü é")
    ).encode("latin-1"),
    "utf8_bom": b"\xef\xbb\xbf" + TEXT["viewbox_only"].encode("utf-8"),
}

SVGZ: dict[str, bytes] = {"svgz_gradients": gzip.compress(TEXT["gradients"].encode("utf-8"))}


def write_all(folder) -> dict[str, "object"]:
    """Hepsini `folder` içine yaz; ad -> yol. Göreli görüntü örneği için red8.png de yazılır."""
    from pathlib import Path

    folder = Path(folder)
    out = {}
    (folder / "red8.png").write_bytes(_png(8, 8, (255, 0, 0)))
    for name, text in TEXT.items():
        path = folder / f"{name}.svg"
        path.write_text(text, encoding="utf-8")
        out[name] = path
    for name, data in {**BROKEN, **ENCODED}.items():
        path = folder / f"{name}.svg"
        path.write_bytes(data)
        out[name] = path
    for name, data in SVGZ.items():
        path = folder / f"{name}.svgz"
        path.write_bytes(data)
        out[name] = path
    return out
