---
name: api-designer
context: fork
agent: api-designer
description: Design REST APIs with proper conventions, request/response schemas, and OpenAPI docs. Use when creating endpoints, designing schemas, generating API documentation, or creating API collections. Triggers on "API design", "create endpoint", "schema", "pydantic", "openapi", "API documentation".
phase: planning
flow-next: writing-plans
flow-alternatives: [git-worktrees, coder, test-generator]
related: [architect, context7]
---

# API Designer

## Task

$ARGUMENTS

Carry out the request above using the guidance in this skill. When finished, STOP and emit the Context Summary + Next Steps.

## Overview

Design REST APIs following conventions with request/response schemas and OpenAPI documentation. Framework-agnostic with Python idioms — examples use Pydantic + FastAPI, but the conventions apply equally to Django REST Framework serializers or Flask + marshmallow/apispec.

Most Python web frameworks generate OpenAPI/Swagger automatically from typed schemas (FastAPI built-in; DRF via drf-spectacular; Flask via flask-smorest/apispec) — so design the schemas, and the docs follow.

> **Documentation lookup:** for current framework-specific OpenAPI-generation and response-model patterns, consult [[/context7]] (`npx -y ctx7 …`) before committing to an approach.

## Generated File Naming Convention (MANDATORY)

**ANY file created by this skill MUST be prefixed with `api-designer-`.**
Predefined output (`api-designer-spec.md`) already follows this convention.
Any additional ad-hoc files (summaries, notes, collections) MUST also follow this rule:
- ✅ `api-designer-http-collection.md`
- ❌ `HTTP_COLLECTION.md`

## REST Conventions

### URL Naming

```
# Resources (nouns, plural)
GET    /users           # List
POST   /users           # Create
GET    /users/:id       # Get one
PUT    /users/:id       # Full update
PATCH  /users/:id       # Partial update
DELETE /users/:id       # Delete

# Nested resources
GET    /users/:id/posts     # User's posts
POST   /users/:id/posts     # Create post for user

# Actions (verbs for non-CRUD)
POST   /users/:id/activate
POST   /orders/:id/cancel
```

### HTTP Methods & Status Codes

| Method | Success | Purpose |
|--------|---------|---------|
| GET | 200 | Retrieve resource(s) |
| POST | 201 | Create resource |
| PUT | 200 | Full update |
| PATCH | 200 | Partial update |
| DELETE | 204 | Delete resource |

### Error Responses

```json
{
  "status_code": 400,
  "message": "Validation failed",
  "error": "Bad Request",
  "error_code": "VALIDATION_ERROR",
  "details": [
    { "field": "email", "message": "Invalid email format" }
  ]
}
```

## Schema Patterns (Pydantic)

### Request Schemas

```python
# schemas/user.py
from pydantic import BaseModel, EmailStr, Field


class CreateUser(BaseModel):
    email: EmailStr = Field(max_length=255, examples=["john@example.com"])
    first_name: str = Field(min_length=1, max_length=100, examples=["John"])
    title: str | None = Field(default=None, max_length=255)
```

### Update Schemas

```python
# All fields optional; email is immutable so it is omitted
class UpdateUser(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    title: str | None = Field(default=None, max_length=255)
```

### Response Schemas

```python
from pydantic import BaseModel, ConfigDict


class UserResponse(BaseModel):
    # Read attributes directly off ORM objects
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    first_name: str
    # Note: password / password_hash is simply absent → never serialized
```

The field validators (`EmailStr`, `Field(max_length=...)`) double as both runtime validation and OpenAPI schema metadata — no separate decorators needed.

## OpenAPI Documentation

Documentation is generated from the typed signatures. In FastAPI, the `response_model`, status codes, summary, and tags live on the route decorator:

```python
@router.post(
    "",
    response_model=UserResponse,
    status_code=201,
    summary="Create a new user",
    responses={
        400: {"description": "Validation error"},
        409: {"description": "Email already exists"},
    },
)
async def create_user(payload: CreateUser, service: UserService = Depends(get_user_service)):
    ...


@router.get("/{user_id}", response_model=UserResponse, summary="Get user by ID",
            responses={404: {"description": "User not found"}})
async def get_user(user_id: int, service: UserService = Depends(get_user_service)):
    ...
```

(DRF: annotate with `@extend_schema` from drf-spectacular. Flask-smorest: `@blp.response(...)` / `@blp.arguments(...)`.)

### Query Parameters

```python
from fastapi import Query


@router.get("", response_model=list[UserResponse], summary="List users with pagination")
async def list_users(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    search: str | None = Query(None),
    service: UserService = Depends(get_user_service),
):
    ...
```

## Router Template (FastAPI)

```python
from fastapi import APIRouter, Depends

from app.schemas.user import CreateUser, UserResponse
from app.services.user import UserService
from app.dependencies import get_user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserResponse, status_code=201, summary="Create user")
async def create(payload: CreateUser, service: UserService = Depends(get_user_service)):
    return await service.create(payload)


@router.get("/{user_id}", response_model=UserResponse, summary="Get user by ID")
async def get_one(user_id: int, service: UserService = Depends(get_user_service)):
    return await service.get_by_id(user_id)
```

Keep route handlers thin: validate input (via the schema), delegate to the service, return the result. Business logic does not belong in handlers.

---

## Final Output (MANDATORY)

**Before presenting next steps, you MUST update the living specification:**

### Living Specification Update Process

1. **Read project context:**
   - Read `specs/MANIFEST.md` to understand the project overview
   - Read `specs/architect-architecture.md` to understand system architecture

2. **Read existing API spec:**
   - If `specs/api-designer-spec.md` exists, read it to understand existing endpoints
   - If it doesn't exist, you'll create it using the structure from `../../../spec-desc.md`

3. **Get task number:**
   - If coming from previous skills, use the task number from context (e.g., "TASK-001")
   - Task number should be included in all section headers

4. **Update api-designer-spec.md:**
   - **If file doesn't exist:** Create using the structure template from spec-desc.md
   - **If file exists:** Append new endpoints with `### [TASK-N] HTTP_METHOD /path` prefix
   - Include date in section: `### [TASK-001] POST /auth/login (2026-01-22)`
   - Organize endpoints by module/domain
   - Document:
     - REST endpoints with HTTP methods
     - Request schemas with validation rules
     - Response schemas with examples
     - Error responses and status codes
     - OpenAPI metadata (summary, tags, response models)
     - Router/handler structure outline
   - Keep schemas grouped logically by domain

5. **Update MANIFEST.md if needed:**
   - Update "Last Updated" date for api-designer-spec.md entry
   - Add new API domains to "Key Decisions" section if applicable

**Example section in api-designer-spec.md:**
```markdown
### [TASK-001] POST /auth/login (2026-01-22)

**Domain:** Auth

**Request schema:**
```python
class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)
```

**Response schema:**
```python
class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    user: UserResponse
```

**Status Codes:**
- 200: Success
- 401: Invalid credentials
- 429: Too many attempts
```

This incremental update ensures living documentation that grows with the project.

---

## Next Steps

After updating specs/api-designer-spec.md and specs/MANIFEST.md, STOP and present these options:

**Next by flow:** [[/writing-plans]] `<TASK-{N}: one-line summary of what you did>` - Create implementation tasks from the API specification. See [[moc-planning]] for phase context.

**Pass to next skill:** Include the task number in your context summary (e.g., "TASK-001: Authentication API endpoints documented")

**Alternatives:**
- [[/git-worktrees]] `<TASK-{N}: one-line summary of what you did>` - Create isolated workspace for implementation.
- [[/coder]] `<TASK-{N}: one-line summary of what you did>` - Implement the API directly in current workspace.
- [[/test-generator]] `<TASK-{N}: one-line summary of what you did>` - Generate API integration tests first (TDD approach).
