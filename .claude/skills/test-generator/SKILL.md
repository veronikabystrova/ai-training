---
name: test-generator
context: fork
agent: test-generator
description: Generate comprehensive tests (unit, integration, E2E) and run test suites with failure analysis. Use for writing tests, test coverage, running tests, and fixing failures. Triggers on "write test", "create test", "run tests", "fix test", "test coverage", "e2e test".
phase: execution
flow-next: verify
flow-alternatives: [debugger, docs-generator, finishing-branch, coder]
related: [code-reviewer, debugger]
---

# Test Generator

## Task

$ARGUMENTS

Carry out the request above using the guidance in this skill. When finished, STOP and emit the Context Summary + Next Steps.

## Overview

Generate comprehensive tests (unit, integration, E2E) with **pytest**, following project patterns and TDD principles. Examples use FastAPI's `TestClient`/`httpx` and `unittest.mock`; adapt to Django's test client or Flask's as needed.

## Generated File Naming Convention (MANDATORY)

**ANY documentation or markdown file created by this skill MUST be prefixed with `test-generator-`:**
- ✅ `test-generator-coverage-report.md`, `test-generator-strategy.md`
- ❌ `COVERAGE_REPORT.md`, `TEST_STRATEGY.md`

This applies to ALL generated documentation files — reports, summaries, strategies.
Test source files (`test_*.py`) follow standard pytest naming conventions and are exempt.

## Test Types Decision Tree

```
What are you testing?
        │
        ├── Single unit (handler, service, utility)?
        │   └── Unit Test
        │
        ├── Database/API interaction?
        │   └── Integration Test
        │
        └── User workflow across system?
            └── E2E Test
```

## Unit Tests

### Service Test (mocked repository)

```python
# tests/services/test_user_service.py
import pytest
from unittest.mock import AsyncMock

from app.services.user import UserService
from app.exceptions import ConflictError
from app.schemas.user import CreateUser


@pytest.fixture
def repository() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def service(repository: AsyncMock) -> UserService:
    return UserService(repository)


class TestCreateUser:
    async def test_creates_user_when_email_is_unique(
        self, service: UserService, repository: AsyncMock
    ) -> None:
        # Arrange
        repository.email_exists.return_value = False
        payload = CreateUser(email="test@example.com", first_name="John")

        # Act
        result = await service.create(payload)

        # Assert
        repository.email_exists.assert_awaited_once_with("test@example.com")
        repository.add.assert_awaited_once()
        assert result.email == "test@example.com"

    async def test_raises_conflict_when_email_exists(
        self, service: UserService, repository: AsyncMock
    ) -> None:
        repository.email_exists.return_value = True
        payload = CreateUser(email="existing@example.com", first_name="John")

        with pytest.raises(ConflictError):
            await service.create(payload)
```

> Async tests need an async runner — enable `asyncio_mode = "auto"` under `[tool.pytest.ini_options]`
> (or use `anyio`/`pytest-asyncio` markers).

## Integration Tests

### Repository Test (real DB session via fixture)

```python
# tests/repositories/test_user_repository.py
import pytest

from app.models.user import User
from app.repositories.user import UserRepository


@pytest.fixture
async def repository(db_session) -> UserRepository:
    # db_session is a transactional session fixture defined in conftest.py
    return UserRepository(db_session)


async def test_saves_and_finds_user(repository: UserRepository) -> None:
    # Arrange
    user = User(email="test@example.com", first_name="John")

    # Act
    await repository.add(user)
    found = await repository.find_by_email("test@example.com")

    # Assert
    assert found is not None
    assert found.email == "test@example.com"
```

## E2E Tests

### API Endpoint Test (FastAPI TestClient)

```python
# tests/api/test_users.py
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_creates_a_new_user() -> None:
    response = client.post(
        "/users", json={"email": "test@example.com", "first_name": "John"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"]
    assert body["email"] == "test@example.com"


def test_returns_422_for_invalid_email() -> None:
    response = client.post(
        "/users", json={"email": "invalid-email", "first_name": "John"}
    )

    assert response.status_code == 422  # FastAPI/Pydantic validation error
```

## Test Commands

```bash
# Run all tests
pytest                    # or: uv run pytest

# Run a specific file or test
pytest tests/api/test_users.py
pytest tests/api/test_users.py::test_creates_a_new_user

# Run with coverage
pytest --cov

# Stop at first failure, verbose
pytest -x -v

# Run tests matching a keyword
pytest -k "create_user"
```

## Test Organization

```
tests/
├── conftest.py        # shared fixtures (db_session, client, factories)
├── factories.py       # test-data builders
├── services/
│   └── test_user_service.py
├── repositories/
│   └── test_user_repository.py
└── api/
    └── test_users.py
```

### Factory / Fixture Example

```python
# tests/factories.py
from app.models.user import User


def make_user(**overrides) -> User:
    defaults = {"id": 1, "email": "test@example.com", "first_name": "John"}
    return User(**{**defaults, **overrides})
```

```python
# tests/conftest.py
import pytest


@pytest.fixture
def user():
    from tests.factories import make_user

    return make_user()
```

## Pre-Completion Verification

Run applicable checks from [DOD.md](../../DOD.md) (Standard tier):
- Run all generated tests — verify they pass
- Run lint on test files (if lint tooling exists)
- Verify test file naming follows project conventions
- If tests fail, fix or escalate to `/debugger`
- Include pass/fail evidence in Context Summary

## Quality Checklist

- [ ] Tests are independent (no shared state)
- [ ] Tests have descriptive names
- [ ] Each test tests one thing
- [ ] Edge cases covered
- [ ] Error cases covered
- [ ] Mocks used appropriately
- [ ] No flaky tests

---

## Next Steps

After tests are generated, STOP and present these options:

**Next by flow:** [[/verify]] `<TASK-ID: one-line summary of what you did>` - Run the DoD checklist now that tests pass. See [[moc-execution]] for phase context.

**Alternatives:**
- [[/debugger]] `<TASK-ID: one-line summary of what you did>` - Debug the root cause when tests fail (use instead of /verify on a red suite).
- [[/docs-generator]] `<TASK-ID: one-line summary of what you did>` - Update documentation if all tests pass.
- [[/finishing-branch]] `<TASK-ID: one-line summary of what you did>` - Complete the branch if all tests pass.
- [[/coder]] `<TASK-ID: one-line summary of what you did>` - Fix implementation issues found during testing.
