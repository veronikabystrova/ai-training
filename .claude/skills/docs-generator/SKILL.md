---
name: docs-generator
context: fork
agent: docs-generator
description: Generate and maintain project documentation including READMEs, ADRs, and code docs. Use when creating documentation, updating READMEs, writing ADRs, or documenting modules/packages. Triggers on "document", "README", "ADR", "docstring", "update docs".
phase: finalization
flow-next: finishing-branch
flow-alternatives: [code-reviewer]
related: [architect, api-designer]
---

# Documentation Generator

## Task

$ARGUMENTS

Carry out the request above using the guidance in this skill. When finished, STOP and emit the Context Summary + Next Steps.

## Overview

Generate and maintain project documentation: READMEs, ADRs, and docstrings.

## Generated File Naming Convention (MANDATORY)

**ANY file created by this skill MUST be prefixed with `docs-generator-`.**
Predefined output (`docs-generator-implementation.md`) already follows this convention.
Any additional ad-hoc files (summaries, notes) MUST also follow this rule:
- ✅ `docs-generator-api-reference.md`
- ❌ `API_REFERENCE.md`

Standard project files (`README.md`) are exempt from this rule.

## Living Specification Updates

### specs/docs-generator-implementation.md

**Purpose:** Documents implementation details including build process, deployment, tooling, and development workflows.

**Update when:**
- Build configuration changes
- Deployment process changes
- Development tooling updates
- Environment setup changes

**Read context first:**
- Read `specs/MANIFEST.md` for project overview
- Read all other specs (architect-architecture, api-designer-spec) for context
- Read source code to understand current implementation

**Structure template:** Use `../../../spec-desc.md` if creating from scratch

**Update pattern:** Append sections with `### [TASK-N] Feature Name` prefix

**Example section:**
```markdown
### [TASK-001] CI/CD Pipeline (2026-01-22)

**Pipeline:** GitHub Actions

**Steps:**
1. Lint
2. Test
3. Build
4. Deploy to staging

**Configuration:** `.github/workflows/deploy.yml`
```

## Documentation Types

### 1. Package / Module README

```markdown
# <package-name>

## Overview
[1-2 sentences describing purpose]

## Installation
\`\`\`bash
uv add <package-name>      # or: pip install <package-name>
\`\`\`

## Usage

### Basic Usage
\`\`\`python
from <package> import something
# Example code
\`\`\`

### Advanced Usage
[Additional patterns]

## API Reference

### `function_name`
[Description]

**Parameters:**
- `param1` (type) - Description

**Returns:** type - Description

**Example:**
\`\`\`python
# Usage example
\`\`\`

## Configuration
[Environment variables, settings]

## Testing
\`\`\`bash
pytest
\`\`\`
```

### 2. Feature / Domain README

```markdown
# <feature-name>

## Overview
[Business domain description]

## Architecture

### Layers
- **Models**: ORM models / domain entities
- **Schemas**: request/response models (Pydantic / dataclasses)
- **Repositories**: data access
- **Services**: business logic
- **API**: route handlers / views

### Directory Structure
\`\`\`
<package>/<feature>/
├── models.py
├── schemas.py
├── repository.py
├── service.py
└── api.py
\`\`\`

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /resource | Create |
| GET | /resource/{id} | Get by ID |

## Services
| Service | Purpose | Methods |
|---------|---------|---------|

## Endpoints
| Resource | Path | Purpose |
|----------|------|---------|

## Usage

### Creating a Resource
\`\`\`python
resource = await resource_service.create(...)
\`\`\`
```

### 3. Architecture Decision Record (ADR)

```markdown
# ADR-NNNN: [Title]

## Status
[Proposed | Accepted | Deprecated | Superseded]

## Context
[What is the issue that we're seeing that is motivating this decision?]

## Decision
[What is the change that we're proposing and/or doing?]

## Consequences

### Positive
- [Benefit 1]
- [Benefit 2]

### Negative
- [Drawback 1]
- [Drawback 2]

### Neutral
- [Neutral consequence]

## References
- [Link to relevant docs/discussions]
```

### 4. Docstrings

Use a consistent docstring style (Google, NumPy, or reST). Google style shown:

```python
def function_name(param1: str, param2: int = 0) -> ReturnType:
    """Brief description of what this does.

    Longer description if needed.

    Args:
        param1: Parameter description.
        param2: Parameter description.

    Returns:
        Description of the return value.

    Raises:
        ErrorType: When this error occurs.

    Example:
        >>> result = function_name("a", 1)
    """
```

## Documentation Checklist

### New Package / Module
- [ ] README.md with usage examples
- [ ] Public API documented
- [ ] `__all__` declares the public API (where applicable)

### New Feature
- [ ] README updated
- [ ] ADR if architectural decision
- [ ] Docstrings for public functions

### Code Changes
- [ ] Docstrings updated if signature/behavior changed
- [ ] README updated if usage changed
- [ ] CLAUDE.md updated if new patterns

## CLAUDE.md Updates

When you discover new patterns:

```markdown
## [Pattern Name]

**When to use:** [Conditions]

**Structure:**
\`\`\`python
# Template code
\`\`\`

**Example:**
\`\`\`python
# Real implementation
\`\`\`
```

## Documentation Generation Process

1. **Read living specifications:**
   - Start with `specs/MANIFEST.md` for overview
   - Read all spec files (architect-architecture, api-designer-spec) for context
   - Read source code to understand implementation

2. **Update all documentation:**
   - `specs/docs-generator-implementation.md` - Implementation details (build, deploy, tooling)
   - `README.md` - Project overview and setup
   - `docs/adrs/` - Architecture decisions
   - Docstrings - Code documentation
   - `CLAUDE.md` - Development patterns

3. **Ensure consistency:**
   - Cross-reference between docs
   - Update dates in MANIFEST.md
   - Keep examples accurate with current code

---

## Next Steps

After documentation is complete, STOP and present these options:

**Next by flow:** [[/finishing-branch]] `<TASK-ID: one-line summary of what you did>` - Complete the branch and prepare for merge/PR. See [[moc-finalization]] for phase context.

**Alternatives:**
- [[/code-reviewer]] `<TASK-ID: one-line summary of what you did>` - Review documentation accuracy before finishing.
