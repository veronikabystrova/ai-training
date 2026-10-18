import pytest

from app.core.errors import UnprocessableError
from app.core.svg import MAX_DEPTH, sanitize_svg

OK = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
    b'<rect width="10" height="10" fill="#fff"/></svg>'
)


def test_clean_svg_roundtrips() -> None:
    out = sanitize_svg(OK)
    assert b"<rect" in out
    assert b'viewBox="0 0 10 10"' in out
    assert b"xmlns" in out


@pytest.mark.parametrize(
    "payload",
    [
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><div/></foreignObject></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="x()"><rect/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"><rect/></a></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><rect fill="url(javascript:alert(1))"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><rect onclick="x()" width="1"/></svg>',
    ],
)
def test_hostile_elements_and_attrs_are_stripped(payload: bytes) -> None:
    out = sanitize_svg(payload).lower()
    for bad in (b"<script", b"foreignobject", b"onload", b"onclick", b"javascript:"):
        assert bad not in out


def test_xxe_and_entity_bomb_rejected() -> None:
    xxe = (
        b'<?xml version="1.0"?><!DOCTYPE s [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b'<svg xmlns="http://www.w3.org/2000/svg">&x;</svg>'
    )
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        b'<svg xmlns="http://www.w3.org/2000/svg">&b;</svg>'
    )
    for p in (xxe, bomb):
        with pytest.raises(UnprocessableError):
            sanitize_svg(p)


def test_non_svg_root_rejected() -> None:
    with pytest.raises(UnprocessableError) as e:
        sanitize_svg(b"<html/>")
    assert e.value.code == "INVALID_LOGO"


def test_malformed_xml_rejected() -> None:
    with pytest.raises(UnprocessableError) as e:
        sanitize_svg(b"<svg")
    assert e.value.code == "INVALID_LOGO"


def _nested(depth: int) -> bytes:
    return ("<svg>" + "<g>" * depth + "</g>" * depth + "</svg>").encode()


def test_nesting_at_max_depth_is_accepted() -> None:
    assert sanitize_svg(_nested(MAX_DEPTH)).startswith(b"<svg")


@pytest.mark.parametrize("depth", [MAX_DEPTH + 1, 5000])
def test_deeply_nested_svg_is_rejected_not_crashed(depth: int) -> None:
    with pytest.raises(UnprocessableError) as exc:
        sanitize_svg(_nested(depth))
    assert exc.value.code == "INVALID_LOGO"
