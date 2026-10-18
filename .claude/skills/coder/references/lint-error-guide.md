# Lint Error Navigation Guide

Common `ruff` and `mypy` errors and how to fix them. Assumes the project toolchain: **uv, ruff, pytest, mypy**.

## Ruff Errors (lint)

Ruff bundles pyflakes (`F`), pycodestyle (`E`/`W`), isort (`I`), pyupgrade (`UP`), flake8-bugbear (`B`), and more.

| Code | Meaning | Fix |
|------|---------|-----|
| `F401` | Imported but unused | Remove the import (or re-export via `__all__`) |
| `F841` | Local variable assigned but never used | Remove it, or prefix with `_` |
| `F821` | Undefined name | Add the import or fix the typo |
| `E501` | Line too long | Let `ruff format` wrap it; don't hand-format |
| `I001` | Import block unsorted/unformatted | Run `ruff check --fix` (applies isort) |
| `UP006`/`UP007` | Use `list`/`X \| None` instead of `List`/`Optional` | Modernize the annotation |
| `B008` | Function call in default argument | Use `None` default + set inside the body (or `Depends()` in FastAPI, which is allowlisted) |
| `B904` | `raise` without `from` inside `except` | Use `raise NewError(...) from err` |
| `E722` | Bare `except:` | Catch a specific exception type |

## Mypy Errors (types)

| Pattern | Meaning | Fix |
|---------|---------|-----|
| `Function is missing a return type annotation` | Untyped public function | Add `-> T` (including `-> None`) |
| `Incompatible return value type` | Returned value doesn't match annotation | Fix the value or the annotation |
| `Argument has incompatible type` | Wrong type passed to a call | Fix the argument or widen the signature |
| `Item "None" of "X | None" has no attribute ...` | Accessing through an unchecked optional | Narrow with `if x is not None:` first |
| `Need type annotation for ...` | Inference failed (e.g. empty collection) | Annotate explicitly: `items: list[str] = []` |
| `Returning Any from function declared to return T` | A value typed `Any` is leaking | Add a precise type or a cast with a comment |

## Framework-Specific Hints

| Symptom | Fix |
|---------|-----|
| FastAPI: dependency not injected | Ensure the `Depends(...)` provider is importable and returns the right type |
| Pydantic v2: `from_attributes` needed to read ORM objects | Add `model_config = ConfigDict(from_attributes=True)` |
| SQLAlchemy 2.0: `Mapped[...]` type errors | Use `Mapped[T]` + `mapped_column(...)`, not bare class attributes |
| Django: mypy false positives on models | Use `django-stubs` and configure the mypy plugin |

## Quick Fix Commands

```bash
# Auto-fix lint issues (safe fixes)
ruff check --fix .

# Apply formatting
ruff format .

# Type-check without running
mypy .

# Run a single rule's autofix
ruff check --select I --fix .   # e.g. sort imports only
```
