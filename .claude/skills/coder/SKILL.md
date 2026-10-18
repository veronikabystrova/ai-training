---
name: coder
description: Implement features, fix bugs, and refactor code following a layered architecture (API/handlers → services → data access). Use for feature implementation, bug fixes, refactoring, and business logic development. Triggers on "implement", "add feature", "fix", "refactor", "code", "build".
context: fork
agent: coder
phase: execution
flow-next: code-reviewer
flow-alternatives: [test-generator, debugger]
related: [architect, context7]
---

# Coder

## Task

$ARGUMENTS

Implement the request above by following the workflow, architecture rules, and quality
gates defined below. When done, STOP and emit the Context Summary + Next Steps.

## Overview

Implement features, fix bugs, and refactor Python code following the project's architecture and conventions. Framework-agnostic: the patterns below apply whether the project uses FastAPI, Django, Flask, or no web framework at all — only the outermost "handler" layer differs.

Assumes the project toolchain: **uv, ruff, pytest, mypy**. Follow [GOLDEN-PRINCIPLES.md](../../GOLDEN-PRINCIPLES.md).

**Related skills:**
- `architect` - Architecture decisions
- `api-designer` - API design and request/response schemas
- `context7` - Fetch up-to-date library/framework docs before coding against an unfamiliar API

## Project Structure

Use a conventional Python layout. Match whatever the project already uses; if starting fresh, prefer a `src/` layout:

```
project-root/
├── src/<package>/        ← application code
│   ├── api/              ← route handlers / controllers (framework entrypoints)
│   ├── services/         ← business logic
│   ├── repositories/     ← data access
│   ├── models/           ← ORM models / domain entities
│   └── schemas/          ← request/response schemas (Pydantic / dataclasses)
├── tests/                ← test suite (mirrors src/ layout)
├── pyproject.toml        ← project + tool config (uv, ruff, mypy, pytest)
└── README.md
```

This is a guideline, not a mandate — Django projects use apps with `models.py`/`views.py`/`serializers.py`; small Flask apps may be flatter. Respect the existing structure.

## Generated File Naming Convention (MANDATORY)

**ANY documentation or markdown file created by this skill MUST be prefixed with `coder-`:**
- ✅ `coder-docker-setup.md`, `coder-implementation-summary.md`, `coder-migration-notes.md`
- ❌ `DOCKER_SETUP.md`, `IMPLEMENTATION_SUMMARY.md`, `MIGRATION_NOTES.md`

This applies to ALL generated documentation files — summaries, setup guides, notes, reports.
Python source files (`.py`) follow standard project naming conventions and are exempt.

## Implementation Workflow

```
1. UNDERSTAND → 2. PLAN → 3. IMPLEMENT → 4. TEST → 5. REVIEW
```

### Step 1: Understand

- Review existing codebase patterns
- Identify relevant modules and structure
- Understand the current implementation
- If implementing against an unfamiliar or fast-moving library API, fetch current docs with [[/context7]] (`npx -y ctx7 …`) before coding

### Step 2: Plan

- Identify files to create/modify
- Determine architecture layer placement
- Plan tests needed
- Consider edge cases

### Step 3: Implement

Follow established patterns:
- Use consistent module/package structure
- Add precise type hints (no bare `Any`)
- Handle errors with domain-specific exceptions
- Keep the layering: handlers stay thin, business logic in services, data access in repositories

### Step 4: Test (Verify-Fix Loop)

- Write unit tests with pytest
- Run tests: `pytest` (or `uv run pytest`)
- **If tests fail:** Read FULL error output → Fix root cause → Re-run tests
- **Max 3 attempts.** If still failing after 3 fixes, escalate to `/debugger`
- MUST NOT proceed to completion with failing tests

### Step 5: Pre-Completion Verification

Run applicable checks from [DOD.md](../../DOD.md) (Standard tier):
- Tests: `pytest`
- Lint: `ruff check .`
- Format: `ruff format --check .`
- Types: `mypy .` (if configured)
- Verify file naming follows skill-prefix convention
- If any check fails, fix or escalate — do not skip
- Include verification evidence in Context Summary

## Architecture Compliance

### Layer Placement (Layered Architecture)

| Component | Layer | Typical Location |
|-----------|-------|------------------|
| Route handler / view | Presentation | `api/` (FastAPI routers, Flask blueprints, Django views) |
| Service | Business Logic | `services/` |
| Repository | Data Access | `repositories/` |
| Schema (DTO) | Data Transfer | `schemas/` (Pydantic / dataclasses) |
| Model / Entity | Domain | `models/` |

### Dependency Rules

```
✅ ALLOWED:
handler → service → repository
any layer → shared utilities

❌ NOT ALLOWED:
repository → service
service → handler
```

## Code Patterns

These examples use SQLAlchemy + Pydantic as common choices. Swap for Django ORM/serializers or plain dataclasses as the project dictates — the layering is what matters.

### Model (SQLAlchemy 2.0 style)

```python
from decimal import Decimal

from sqlalchemy import String, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
```

### Schema (Pydantic)

```python
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CreateProduct(BaseModel):
    # Validate at the boundary — reject bad input before it reaches the service
    sku: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=255)
    price: Decimal = Field(gt=0)


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    name: str
    price: Decimal
```

### Repository

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product


class ProductRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_sku(self, sku: str) -> Product | None:
        result = await self._session.execute(
            select(Product).where(Product.sku == sku)
        )
        return result.scalar_one_or_none()

    async def add(self, product: Product) -> Product:
        self._session.add(product)
        await self._session.flush()
        return product
```

### Service

```python
from app.repositories.product import ProductRepository
from app.exceptions import NotFoundError


class ProductService:
    def __init__(self, repository: ProductRepository) -> None:
        self._repository = repository

    async def get_by_sku(self, sku: str) -> Product:
        product = await self._repository.find_by_sku(sku)
        if product is None:
            raise NotFoundError(f"Product {sku!r} not found")
        return product
```

### Route handler (FastAPI shown; adapt to your framework)

```python
from fastapi import APIRouter, Depends

from app.dependencies import get_product_service
from app.schemas.product import CreateProduct, ProductResponse
from app.services.product import ProductService

router = APIRouter(prefix="/products", tags=["products"])


@router.get("/{sku}", response_model=ProductResponse)
async def get_product(sku: str, service: ProductService = Depends(get_product_service)):
    return await service.get_by_sku(sku)


@router.post("", response_model=ProductResponse, status_code=201)
async def create_product(
    payload: CreateProduct, service: ProductService = Depends(get_product_service)
):
    return await service.create(payload)
```

## File Naming Conventions

| Type | Pattern | Example |
|------|---------|---------|
| Module | `snake_case.py` | `product_service.py` |
| Model | `<name>.py` in `models/` | `models/product.py` |
| Repository | `<name>.py` in `repositories/` | `repositories/product.py` |
| Service | `<name>.py` in `services/` | `services/product.py` |
| Schema | `<name>.py` in `schemas/` | `schemas/product.py` |
| Test | `test_<name>.py` | `tests/test_product_service.py` |

## Quality Checklist

Before completing any implementation, verify against [DOD.md](../../DOD.md):

- [ ] Code follows project conventions
- [ ] Proper error handling with domain-specific exceptions
- [ ] Type hints are precise (no bare `Any`)
- [ ] Unit tests added (`test_*.py`)
- [ ] Lint passes (`ruff check .`)
- [ ] Formatting clean (`ruff format --check .`)
- [ ] Types pass (`mypy .`)
- [ ] Tests pass (`pytest`)
- [ ] File naming uses `coder-` prefix for any generated docs

---

## Next Steps

After implementation is complete, STOP and present these options:

**Next by flow:** [[/code-reviewer]] `<TASK-ID: one-line summary of what you did>` - Review the implemented code for quality and issues. See [[moc-execution]] for phase context.

**Alternatives:**
- [[/test-generator]] `<TASK-ID: one-line summary of what you did>` - Generate tests for the implementation.
- [[/debugger]] `<TASK-ID: one-line summary of what you did>` - Debug if there are issues with the implementation.
