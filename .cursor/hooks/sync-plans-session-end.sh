#!/usr/bin/env bash
set -euo pipefail

# sessionEnd: ~/.cursor/plans -> Git docs/plans (+ commit/push if changed)
cat > /dev/null

if [[ -n "${CURSOR_PROJECT_DIR:-}" ]]; then
  cd "$CURSOR_PROJECT_DIR"
elif [[ -f ".cursor/hooks.json" ]]; then
  :
else
  exit 0
fi

./scripts/sync-plans.sh push || true
