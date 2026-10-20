"""OpenAPI post-processing: every error response documents the shared error envelope."""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from app.core.errors import ErrorResponse

_ENVELOPE_REF = {"$ref": "#/components/schemas/ErrorResponse"}


def _describe(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


def install_openapi(app: FastAPI) -> None:
    """Serve a schema where every 4xx/5xx response (incl. 422) is the ``ErrorResponse`` envelope.

    FastAPI's default 422 body is ``HTTPValidationError`` ({"detail": [...]}), which this API never
    returns, and 500 is not declared per route. Both are normalised here instead of on 85 routes.
    """

    def build() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title, version=app.version, routes=app.routes, description=app.description
        )
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        defs = ErrorResponse.model_json_schema(ref_template="#/components/schemas/{model}")
        components.update(defs.pop("$defs", {}))
        components["ErrorResponse"] = defs
        components.pop("HTTPValidationError", None)
        components.pop("ValidationError", None)
        for operations in schema["paths"].values():
            for operation in operations.values():
                responses = operation.setdefault("responses", {})
                responses.setdefault("500", {"description": _describe(500)})
                for code, response in responses.items():
                    if code.isdigit() and int(code) >= 400:
                        # a fresh dict per response: nothing may be shared between operations
                        response["content"] = {"application/json": {"schema": dict(_ENVELOPE_REF)}}
        app.openapi_schema = schema
        return schema

    app.openapi = build  # type: ignore[method-assign]
