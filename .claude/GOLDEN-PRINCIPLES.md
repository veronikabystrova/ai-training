# Golden Principles — Style Invariants

Taste as invariant. These are non-negotiable style rules for all generated Python code.
They assume the project toolchain: **uv, ruff, pytest, mypy**.

## Naming

- Modules / files: `snake_case` (`user_service.py`, not `UserService.py`)
- Packages (directories): short, lowercase, no underscores where avoidable (`payments`, `auth`)
- Classes: `PascalCase` (`UserService`, `CreateUserRequest`)
- Functions / methods / variables: `snake_case` (`find_by_id`, `create_user`)
- Constants: `UPPER_SNAKE_CASE` (`MAX_RETRIES`, `API_BASE_URL`)
- Booleans: prefix with `is_/has_/can_/should_` (`is_active`, `has_permission`)
- Private/internal: single leading underscore (`_internal_helper`); avoid name-mangling `__dunder` unless needed
- Follow PEP 8.

## Typing

- Add type hints to all public functions, methods, and module-level constants
- No bare `Any` — use a precise type, a `Protocol`, a `TypeVar`, or `object` + narrowing
- Don't abuse `# type: ignore` — fix the type or use a narrow, commented ignore (`# type: ignore[arg-type]  # reason`)
- Use `@dataclass` (or Pydantic models when validation/serialization is needed) for structured data, not loose dicts
- Prefer `X | None` over `Optional[X]` (Python 3.10+); annotate return types explicitly, including `-> None`
- `mypy .` must pass with the project's configured strictness

## Error Handling

- Raise domain-specific exceptions (subclasses of a project base, e.g. `NotFoundError`, `ConflictError`)
- Never use a bare `except:` and never swallow exceptions with an empty `except Exception: pass`
- Catch the narrowest exception type that applies
- Log errors with context (what operation, what input) using the `logging` module — not `print`
- At boundaries (API handlers, CLI entrypoints): catch and return a structured error response
- Internal code: let exceptions propagate to the boundary handler
- Use `raise ... from err` to preserve the cause chain

## Imports

- Group per PEP 8 / ruff isort: 1) standard library, 2) third-party, 3) local application
- Absolute imports for cross-package references; relative imports only within a package
- No wildcard imports (`from x import *`)
- No circular imports — if needed, refactor to break the cycle or use a local import

## Test Structure (pytest)

- Test files named `test_*.py`; test functions named `test_<behavior>_when_<condition>`
- Arrange-Act-Assert (AAA) within each test
- Use fixtures for setup/teardown instead of repeated boilerplate
- Parametrize with `@pytest.mark.parametrize` instead of copy-pasting cases
- One concept per test; no interdependence between tests (each sets up its own state)
- Assert on behavior/outputs, not on private internals

## Code Organization

- Prefer one cohesive responsibility per module; group tightly-coupled helpers together
- Keep functions short (~30 lines) — extract when longer
- Keep modules focused (~300 lines) — split when longer
- Early returns over deeply nested conditionals
- Prefer composition over inheritance; use `Protocol` for duck-typed interfaces
- Line length and formatting are owned by `ruff format` (default 88) — don't hand-format against it
