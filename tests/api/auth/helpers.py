from typing import Any

from httpx import AsyncClient

from tests.factories import TEST_PASSWORD

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"


def code(response: Any) -> str:
    return str(response.json()["error"]["code"])


async def login(client: AsyncClient, email: str, password: str = TEST_PASSWORD) -> dict[str, Any]:
    r = await client.post(LOGIN, json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body
