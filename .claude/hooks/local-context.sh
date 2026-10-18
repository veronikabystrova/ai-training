#!/bin/bash
# Local Context Session Start Hook
# Scans working directory at session start to provide project context.
# Hook type: SessionStart
# Exit codes: always 0 (informational only)

echo "📋 Project Context"
echo "=================="

# Git info
if git rev-parse --git-dir > /dev/null 2>&1; then
  BRANCH=$(git branch --show-current 2>/dev/null)
  STATUS=$(git status --short 2>/dev/null | wc -l | tr -d ' ')
  echo "Branch: $BRANCH ($STATUS uncommitted changes)"
fi

# Python interpreter
if command -v python3 >/dev/null 2>&1; then
  echo "Python: $(python3 --version 2>&1)"
elif command -v python >/dev/null 2>&1; then
  echo "Python: $(python --version 2>&1)"
fi

# Active virtualenv
[ -n "$VIRTUAL_ENV" ] && echo "Virtualenv: $VIRTUAL_ENV"
[ -d ".venv" ] && echo "Virtualenv: .venv (local)"

# Dependency / package managers (detect by lock/manifest files)
MANAGERS=""
[ -f "uv.lock" ] && MANAGERS="$MANAGERS uv"
[ -f "poetry.lock" ] && MANAGERS="$MANAGERS poetry"
[ -f "Pipfile.lock" ] && MANAGERS="$MANAGERS pipenv"
[ -f "pdm.lock" ] && MANAGERS="$MANAGERS pdm"
{ [ -f "requirements.txt" ] || [ -f "requirements-dev.txt" ]; } && MANAGERS="$MANAGERS pip"
[ -n "$MANAGERS" ] && echo "Dependency managers:$MANAGERS"
[ -f "pyproject.toml" ] && echo "Project manifest: pyproject.toml"

# Tooling detection (config files or pyproject sections)
TOOLS=""
if [ -f "pyproject.toml" ]; then
  grep -q "\[tool.ruff" pyproject.toml 2>/dev/null && TOOLS="$TOOLS ruff"
  grep -q "\[tool.mypy" pyproject.toml 2>/dev/null && TOOLS="$TOOLS mypy"
  grep -q "\[tool.pytest" pyproject.toml 2>/dev/null && TOOLS="$TOOLS pytest"
fi
{ [ -f "ruff.toml" ] || [ -f ".ruff.toml" ]; } && TOOLS="$TOOLS ruff"
[ -f "mypy.ini" ] && TOOLS="$TOOLS mypy"
{ [ -f "pytest.ini" ] || [ -f "tox.ini" ]; } && TOOLS="$TOOLS pytest"
# De-dupe and report
[ -n "$TOOLS" ] && echo "Tooling:$(echo "$TOOLS" | tr ' ' '\n' | sort -u | tr '\n' ' ' | sed 's/^ *//;s/ *$//' | sed 's/^/ /')"

[ -f "Makefile" ] && echo "Build system: Make"

# Project structure
echo ""
echo "Structure:"
for DIR in src app tests tasks specs .claude; do
  if [ -d "$DIR" ]; then
    COUNT=$(find "$DIR" -maxdepth 1 -type f 2>/dev/null | wc -l | tr -d ' ')
    echo "  $DIR/ ($COUNT files)"
  fi
done

# Task and spec counts
if [ -d "tasks" ]; then
  TASK_COUNT=$(find tasks -maxdepth 1 -type d -name "TASK-*" 2>/dev/null | wc -l | tr -d ' ')
  echo "  Tasks: $TASK_COUNT"
fi
if [ -d "specs" ]; then
  SPEC_COUNT=$(find specs -maxdepth 1 -type f -name "*.md" ! -name "MANIFEST.md" 2>/dev/null | wc -l | tr -d ' ')
  echo "  Specs: $SPEC_COUNT"
fi

exit 0
