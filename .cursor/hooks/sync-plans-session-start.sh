#!/usr/bin/env bash
set -euo pipefail

# sessionStart: git pull 후 Git plans -> ~/.cursor/plans
cat > /dev/null

if [[ -n "${CURSOR_PROJECT_DIR:-}" ]]; then
  cd "$CURSOR_PROJECT_DIR"
elif [[ -f ".cursor/hooks.json" ]]; then
  :
else
  exit 0
fi

git pull --rebase --autostash >/dev/null 2>&1 || true
./scripts/sync-plans.sh pull
