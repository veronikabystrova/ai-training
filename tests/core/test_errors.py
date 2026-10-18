import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException

from app.core.errors import (
    ConflictError,
    NotFoundError,
    UnauthorizedError,
    install_error_handlers,
    map_integrity_error,
)


def make_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    class Body(BaseModel):
        n: int

    @app.get("/nf")
    async def nf() -> None:
        raise NotFoundError()

    @app.get("/conflict")
    async def conflict() -> None:
        raise ConflictError(
            "COACH_ALREADY_ASSIGNED", "taken", details=[{"current_trainer_id": "abc"}]
        )

    @app.get("/integrity")
    async def integrity() -> None:
        raise IntegrityError("stmt", {}, Exception("x"))

    @app.get("/integrity-email")
    async def integrity_email() -> None:
        class Diag:
            constraint_name = "uq_users_email"

        class Orig(Exception):
            diag = Diag()

        raise IntegrityError("stmt", {}, Orig())

    @app.get("/unauth")
    async def unauth() -> None:
        raise HTTPException(401, headers={"WWW-Authenticate": "Bearer"})

    @app.get("/teapot")
    async def teapot() -> None:
        raise HTTPException(599)

    @app.post("/v")
    async def v(b: Body) -> None: ...

    return app


async def test_error_envelope_shape() -> None:
    async with AsyncClient(transport=ASGITransport(app=make_app()), base_url="http://t") as c:
        r = await c.get("/nf")
        assert r.status_code == 404
        assert r.json() == {"error": {"code": "NOT_FOUND", "message": "Not found", "details": []}}
        r = await c.get("/conflict")
        assert r.status_code == 409
        assert r.json()["error"]["details"] == [{"current_trainer_id": "abc"}]


async def test_validation_error_uses_envelope() -> None:
    async with AsyncClient(transport=ASGITransport(app=make_app()), base_url="http://t") as c:
        r = await c.post("/v", json={"n": "x"})
        body = r.json()["error"]
        assert r.status_code == 422
        assert body["code"] == "VALIDATION_ERROR"
        assert body["details"][0]["field"] == "n"


@pytest.mark.parametrize(
    ("constraint", "code"),
    [
        ("uq_users_email", "EMAIL_TAKEN"),
        ("uq_trainer_profiles_slug", "SLUG_TAKEN"),
        ("uq_coach_one_active_trainer", "COACH_ALREADY_ASSIGNED"),
        ("ex_availability_no_overlap", "OVERLAPPING_WINDOWS"),
    ],
)
def test_integrity_error_mapped_by_constraint_name(constraint: str, code: str) -> None:
    class Diag:
        constraint_name = constraint

    class Orig(Exception):
        diag = Diag()

    err = map_integrity_error(IntegrityError("stmt", {}, Orig()))
    assert err is not None
    assert err.code == code


def test_unknown_constraint_is_not_mapped() -> None:
    assert map_integrity_error(IntegrityError("stmt", {}, Exception("x"))) is None


async def test_unknown_route_and_wrong_method_use_envelope() -> None:
    async with AsyncClient(transport=ASGITransport(app=make_app()), base_url="http://t") as c:
        r = await c.get("/missing")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "NOT_FOUND"
        r = await c.post("/nf")
        assert r.status_code == 405
        assert r.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
        assert "allow" in r.headers


async def test_unmapped_integrity_error_returns_500_envelope() -> None:
    # Unhandled exceptions are answered by CorrelationIdMiddleware (tests/api/test_middleware.py).
    async with AsyncClient(transport=ASGITransport(app=make_app()), base_url="http://t") as c:
        r = await c.get("/integrity")
        assert r.status_code == 500
        assert r.json()["error"]["code"] == "INTERNAL_ERROR"


async def test_mapped_integrity_error_returns_409_envelope() -> None:
    async with AsyncClient(transport=ASGITransport(app=make_app()), base_url="http://t") as c:
        r = await c.get("/integrity-email")
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "EMAIL_TAKEN"


async def test_framework_401_uses_invalid_token_and_keeps_headers() -> None:
    async with AsyncClient(transport=ASGITransport(app=make_app()), base_url="http://t") as c:
        r = await c.get("/unauth")
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "INVALID_TOKEN"
        assert r.headers["www-authenticate"] == "Bearer"


async def test_non_standard_http_status_uses_fallback_code() -> None:
    async with AsyncClient(transport=ASGITransport(app=make_app()), base_url="http://t") as c:
        r = await c.get("/teapot")
        assert r.status_code == 599
        assert r.json()["error"] == {"code": "HTTP_599", "message": "Error", "details": []}


def test_unauthorized_error_carries_bearer_challenge() -> None:
    assert UnauthorizedError().headers == {"WWW-Authenticate": "Bearer"}
    assert UnauthorizedError("IMPERSONATION_EXPIRED").headers["WWW-Authenticate"] == "Bearer"
