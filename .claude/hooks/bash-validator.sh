#!/bin/bash
# Bash Validator Hook
# Blocks destructive commands: force-push, hard reset, DROP TABLE, npm publish.
# Hook type: PreToolUse:Bash
# Exit codes: 0 = pass, 1 = warn (continue), 2 = block
#
# Currently in WARN mode (exit 1). Switch to exit 2 after tuning.

INPUT=$(cat)

# Extract command from JSON input (POSIX-compatible, no grep -P)
COMMAND=$(echo "$INPUT" | sed -n 's/.*"command"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)

if [ -z "$COMMAND" ]; then
  exit 0
fi

# BLOCKED patterns — hard block (exit 2), truly destructive/irreversible.
# Matched with grep -E (ERE). The git-push force checks anchor the flag to a
# word boundary so substrings inside args/branch names (e.g. "remove-idea-from-repo")
# don't false-positive.
BLOCKED_PATTERNS=(
  "git push.*--force"
  "git push.* -f( |$)"
  "git reset --hard"
  "git clean -f"
  "DROP TABLE"
  "DROP DATABASE"
  "npm publish"
  "npx publish"
  "rm -rf /"
  "rm -rf ~"
  "rm -rf \."
  "--no-verify"
  "gh repo delete"
  "gh repo archive"
  "gh issue delete"
  "gh release delete"
  "gh api.*DELETE"
)

for PATTERN in "${BLOCKED_PATTERNS[@]}"; do
  if echo "$COMMAND" | grep -qiE -- "$PATTERN"; then
    echo "🚫 BLOCKED: Destructive command detected: matches pattern '$PATTERN'"
    echo "   Command: $COMMAND"
    echo "   This operation is blocked. See AGENTS.md."
    exit 2
  fi
done

# Branch force-delete is destructive — but matched CASE-SENSITIVELY so the safe
# lowercase `git branch -d` (delete a merged branch) is NOT blocked. Only `-D`
# (force-delete) and `--force` are.
if echo "$COMMAND" | grep -qE 'git branch .*(-D|--force)'; then
  echo "🚫 BLOCKED: Destructive command detected: branch force-delete (-D/--force)"
  echo "   Command: $COMMAND"
  echo "   Use 'git branch -d' for a safe (merged-only) delete. See AGENTS.md."
  exit 2
fi

exit 0
