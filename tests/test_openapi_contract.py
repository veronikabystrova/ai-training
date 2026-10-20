"""The served OpenAPI document conforms to specs/api-designer-spec.md.

Runs without containers: the app is built with lazy connections and only ``openapi()`` is read.
"""

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.main import create_app

SPEC = (Path(__file__).parents[1] / "specs" / "api-designer-spec.md").read_text()
API = "/api/v1"
TAGS = {"auth", "me", "me-players", "me-approvals", "trainer", "public", "admin"}
# Tags that are not simply the first path segment.
SEGMENT_TAG = {"auth": "auth", "trainer": "trainer", "public": "public", "admin": "admin"}
METHODS = "GET|POST|PUT|PATCH|DELETE"
HEADING = re.compile(
    rf"^### \[TASK-001\] (?P<routes>(?:{METHODS}) /[^\s,]+(?:, (?:{METHODS}) /[^\s,]+)*)", re.M
)
STATUS_LINE = re.compile(r"\*\*Status Codes:\*\*(?P<body>.*?)(?:\n\s*\n|\n#|\Z)", re.S)


def _spec_sections() -> dict[tuple[str, str], str]:
    """``(METHOD, path) -> markdown of its section`` for every TASK-001 heading."""
    heads = list(HEADING.finditer(SPEC))
    # A section ends at the next heading of any level (### or ##) or horizontal rule.
    boundary = [m.start() for m in re.finditer(r"^#{2,3} ", SPEC, re.M)]
    out: dict[tuple[str, str], str] = {}
    for head in heads:
        end = next((b for b in boundary if b > head.start()), len(SPEC))
        body = SPEC[head.start() : end]
        for route in head["routes"].split(", "):
            method, path = route.split(" ", 1)
            out[(method, API + path)] = body
    return out


def _spec_codes(section: str) -> set[int]:
    """Status codes listed on a section's ``Status Codes`` line(s); empty when it has none."""
    codes: set[int] = set()
    for line in STATUS_LINE.finditer(section):
        for item in re.split(r" · |\n", line["body"]):
            first = re.match(r"[\s\-]*(\d{3})\b(?: / (\d{3})\b)?", item)
            if first:
                codes.update(int(c) for c in first.groups() if c)
    return codes


def _norm(path: str) -> str:
    """Spec writes ``{id}`` / ``{trainer_id}``; the app names every path parameter."""
    return re.sub(r"\{[^}]+\}", "{}", path)


@pytest.fixture(scope="module")
def openapi() -> dict[str, Any]:
    settings = Settings(
        database_url="postgresql+asyncpg://u:p@localhost:5432/d",
        redis_url="redis://localhost:6379/0",
        jwt_secret="k" * 40,
        hash_salt="s" * 16,
    )
    app: FastAPI = create_app(settings)
    return app.openapi()


def _operations(openapi: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (method.upper(), _norm(path)): op
        for path, ops in openapi["paths"].items()
        for method, op in ops.items()
        if path.startswith(API)
    }


def test_openapi_paths_match_spec_headings(openapi: dict[str, Any]) -> None:
    implemented = set(_operations(openapi))
    documented = {(m, _norm(p)) for m, p in _spec_sections()}
    assert implemented - documented == set(), "implemented but not in the spec"
    assert documented - implemented == set(), "in the spec but not implemented"


def test_every_operation_has_summary_and_tag(openapi: dict[str, Any]) -> None:
    for (method, path), op in _operations(openapi).items():
        where = f"{method} {path}"
        assert op.get("summary"), where
        tags = op.get("tags")
        assert tags and len(tags) == 1 and tags[0] in TAGS, where
        segment = path.removeprefix(API).split("/")[1]
        if segment in SEGMENT_TAG:
            assert tags == [SEGMENT_TAG[segment]], where
        elif path.startswith(f"{API}/me/players"):
            assert tags == ["me-players"], where
        elif path.startswith(f"{API}/me/approvals"):
            assert tags == ["me-approvals"], where
        else:
            assert tags == ["me"], where


def test_every_operation_declares_success_response_model(openapi: dict[str, Any]) -> None:
    for (method, path), op in _operations(openapi).items():
        successes = {c: r for c, r in op["responses"].items() if c.startswith("2")}
        assert successes, f"{method} {path}: no 2xx documented"
        for code, response in successes.items():
            if code in {"204", "205"}:
                assert "content" not in response, f"{method} {path} {code}"
            else:
                schema = response["content"]["application/json"]["schema"]
                assert schema, f"{method} {path} {code}: no response schema"


def test_error_envelope_documented_for_every_error_response(openapi: dict[str, Any]) -> None:
    ref = "#/components/schemas/ErrorResponse"
    for (method, path), op in _operations(openapi).items():
        errors = {c: r for c, r in op["responses"].items() if int(c) >= 400}
        assert errors, f"{method} {path}: no error responses"
        for code, response in errors.items():
            schema = response["content"]["application/json"]["schema"]
            assert schema == {"$ref": ref}, f"{method} {path} {code}"
    schema = openapi["components"]["schemas"]
    assert set(schema["ErrorResponse"]["properties"]) == {"error"}
    assert set(schema["ErrorBody"]["properties"]) == {"code", "message", "details"}
    assert "HTTPValidationError" not in schema


def test_status_codes_match_the_spec(openapi: dict[str, Any]) -> None:
    """Spec ``Status Codes`` lines: every listed code is documented and 2xx sets are equal."""
    operations = _operations(openapi)
    problems: list[str] = []
    for (method, path), section in _spec_sections().items():
        spec_codes = _spec_codes(section)
        if not spec_codes:
            continue  # section defers to another ("same as ...")
        have = {int(c) for c in operations[(method, _norm(path))]["responses"]}
        missing = spec_codes - have
        op = operations[(method, _norm(path))]
        if not op.get("parameters") and "requestBody" not in op:
            missing -= {422}  # a grouped section's 422 does not apply to a bodiless route
        if missing:
            problems.append(f"{method} {path}: spec lists {sorted(missing)}, not documented")
        spec_ok = {c for c in spec_codes if c < 300}
        have_ok = {c for c in have if c < 300}
        if spec_ok != have_ok:
            problems.append(f"{method} {path}: 2xx spec {sorted(spec_ok)} != app {sorted(have_ok)}")
    assert problems == []


def test_two_success_routes_document_both_codes(openapi: dict[str, Any]) -> None:
    ops = _operations(openapi)
    assert {"200", "201"} <= set(
        ops[("POST", _norm(f"{API}/me/players/{{id}}/trainers"))]["responses"]
    )
    assert {"200", "202"} <= set(
        ops[("POST", _norm(f"{API}/auth/verify-email/resend"))]["responses"]
    )
    assert {"200", "201"} <= set(ops[("POST", _norm(f"{API}/auth/register"))]["responses"])


def _body_schema(response: dict[str, Any]) -> dict[str, Any]:
    schema: dict[str, Any] = response["content"]["application/json"]["schema"]
    return schema


def test_register_documents_a_separate_body_schema_per_success_code(
    openapi: dict[str, Any],
) -> None:
    responses = _operations(openapi)[("POST", _norm(f"{API}/auth/register"))]["responses"]
    assert _body_schema(responses["201"]) == {"$ref": "#/components/schemas/AuthResponse"}
    assert _body_schema(responses["200"]) == {"$ref": "#/components/schemas/LinkedResponse"}


def test_error_responses_do_not_share_one_content_object(openapi: dict[str, Any]) -> None:
    contents = [
        response["content"]
        for op in _operations(openapi).values()
        for code, response in op["responses"].items()
        if code.isdigit() and int(code) >= 400
    ]
    assert len(contents) > 100
    assert len({id(c) for c in contents}) == len(contents)
    assert len({id(_body_schema({"content": c})) for c in contents}) == len(contents)


def test_bearer_scheme_is_declared_for_swagger_authorize(openapi: dict[str, Any]) -> None:
    schemes = openapi["components"]["securitySchemes"]
    assert any(s == {"type": "http", "scheme": "bearer"} for s in schemes.values())


def test_authenticated_routes_declare_bearer_security(openapi: dict[str, Any]) -> None:
    ops = _operations(openapi)
    assert ops[("GET", f"{API}/me")].get("security")
    assert ops[("POST", f"{API}/auth/register")].get("security")  # optional principal
    assert not ops[("POST", f"{API}/auth/login")].get("security")
