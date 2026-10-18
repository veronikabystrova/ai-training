"""Allowlist-based SVG sanitiser for trainer logos (architecture §4)."""

import xml.etree.ElementTree as ET

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

from app.core.errors import UnprocessableError

SVG_NS = "http://www.w3.org/2000/svg"

_ELEMENTS = frozenset(
    "svg g path rect circle ellipse line polyline polygon defs linearGradient "
    "radialGradient stop clipPath title desc".split()
)
_ATTRIBUTES = frozenset(
    "fill stroke stroke-width opacity transform viewBox d points x y cx cy r rx ry "
    "width height id offset stop-color".split()
)
_FORBIDDEN_VALUE_MARKERS = ("javascript:", "data:")
MAX_DEPTH = 32

ET.register_namespace("", SVG_NS)


def _invalid(message: str) -> UnprocessableError:
    return UnprocessableError("INVALID_LOGO", message)


def _split(tag: str) -> tuple[str, str]:
    """Return (namespace, local name) of an ElementTree tag."""
    if tag.startswith("{"):
        ns, _, local = tag[1:].partition("}")
        return ns, local
    return "", tag


def _attr_allowed(name: str, value: str) -> bool:
    if name.startswith("{") or name.lower().startswith("on") or name not in _ATTRIBUTES:
        return False
    compact = "".join(value.split()).lower()
    return not any(marker in compact for marker in _FORBIDDEN_VALUE_MARKERS)


def _copy(src: ET.Element, parent: ET.Element | None, depth: int = 0) -> ET.Element | None:
    if depth > MAX_DEPTH:
        raise _invalid("Logo is too deeply nested")
    ns, local = _split(src.tag)
    if ns not in ("", SVG_NS) or local not in _ELEMENTS:
        return None  # drop the element together with its whole subtree
    clean = ET.Element(f"{{{SVG_NS}}}{local}")
    for name, value in src.attrib.items():
        if _attr_allowed(name, value):
            clean.set(name, value)
    if local in ("title", "desc") and src.text:
        clean.text = src.text
    if parent is not None:
        parent.append(clean)
    for child in src:
        _copy(child, clean, depth + 1)
    return clean


def sanitize_svg(data: bytes) -> bytes:
    try:
        root = fromstring(data, forbid_dtd=True)
    except (DefusedXmlException, ParseError, ValueError) as exc:
        raise _invalid("Logo is not valid SVG") from exc
    ns, local = _split(root.tag)
    if local != "svg" or ns not in ("", SVG_NS):
        raise _invalid("Logo root element must be <svg>")
    clean = _copy(root, None)
    if clean is None:  # pragma: no cover - root already validated
        raise _invalid("Logo is not valid SVG")
    return bytes(ET.tostring(clean, encoding="utf-8"))
