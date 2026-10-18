#!/bin/bash
# File Naming Validator Hook
# Validates that .md files in tasks/ and specs/ follow skill-prefix naming convention.
# Hook type: PreToolUse:Write, PreToolUse:Edit
# Exit codes: 0 = pass, 1 = warn (continue), 2 = block
#
# BLOCK mode (exit 2): an unprefixed .md write in tasks/ or specs/ is rejected.
# Keep the SKILL_PREFIXES array below in sync when adding/removing skills, or a
# legitimate write may be blocked.

# Read the tool input from stdin
INPUT=$(cat)

# Extract file path from the JSON input (POSIX-compatible, no grep -P)
FILE_PATH=$(echo "$INPUT" | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)

# If no file path found, pass through
if [ -z "$FILE_PATH" ]; then
  exit 0
fi

# Only check .md files
if [[ "$FILE_PATH" != *.md ]]; then
  exit 0
fi

# Only check files in tasks/ and specs/ directories
if [[ "$FILE_PATH" != *tasks/* ]] && [[ "$FILE_PATH" != *specs/* ]]; then
  exit 0
fi

# Extract just the filename
FILENAME=$(basename "$FILE_PATH")

# Allowlist: files that don't need skill prefix
ALLOWLIST=(
  "README.md"
  "MANIFEST.md"
)

for ALLOWED in "${ALLOWLIST[@]}"; do
  if [ "$FILENAME" = "$ALLOWED" ]; then
    exit 0
  fi
done

# Known skill prefixes
SKILL_PREFIXES=(
  "requirements-analyst-"
  "brainstorm-"
  "writing-plans-"
  "architect-"
  "api-designer-"
  "coder-"
  "code-reviewer-"
  "test-generator-"
  "debugger-"
  "finishing-branch-"
  "docs-generator-"
  "skill-creator-"
  "verify-"
  "reflect-"
  "git-worktrees-"
  "context7-"
)

for PREFIX in "${SKILL_PREFIXES[@]}"; do
  if [[ "$FILENAME" == ${PREFIX}* ]]; then
    exit 0
  fi
done

# File doesn't match any skill prefix - BLOCK
echo "🚫 BLOCKED: File naming violation: '$FILENAME' in $(dirname "$FILE_PATH")"
echo "   Expected skill-prefixed name like: {skill-name}-{purpose}.md"
echo "   See AGENTS.md for naming rules."
exit 2
