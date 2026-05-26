#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOCS_PLANS="$REPO_ROOT/docs/plans"
CURSOR_PLANS="${HOME}/.cursor/plans"

usage() {
  echo "usage: $(basename "$0") {pull|push|sync}"
  echo "  pull  Git docs/plans -> ~/.cursor/plans"
  echo "  push  ~/.cursor/plans -> Git docs/plans (+ commit/push if changed)"
  echo "  sync  pull then push (session end용)"
  exit 1
}

ensure_dirs() {
  mkdir -p "$DOCS_PLANS" "$CURSOR_PLANS"
}

pull_plans() {
  ensure_dirs
  shopt -s nullglob
  local copied=0
  for src in "$DOCS_PLANS"/*.plan.md; do
    cp -f "$src" "$CURSOR_PLANS/"
    copied=$((copied + 1))
  done
  for local in "$CURSOR_PLANS"/*.plan.md; do
    base="$(basename "$local")"
    if [[ ! -f "$DOCS_PLANS/$base" ]]; then
      rm -f "$local"
    fi
  done
  echo "[sync-plans] pull complete (${copied} file(s) -> ${CURSOR_PLANS})"
}

push_plans() {
  ensure_dirs
  shopt -s nullglob
  local copied=0
  for src in "$CURSOR_PLANS"/*.plan.md; do
    cp -f "$src" "$DOCS_PLANS/"
    copied=$((copied + 1))
  done
  echo "[sync-plans] copied ${copied} file(s) -> ${DOCS_PLANS}"

  git -C "$REPO_ROOT" add docs/plans
  if git -C "$REPO_ROOT" diff --cached --quiet; then
    echo "[sync-plans] no plan changes to commit"
    return 0
  fi

  git -C "$REPO_ROOT" commit -m "docs: Cursor plans 동기화"
  git -C "$REPO_ROOT" push
  echo "[sync-plans] push complete"
}

case "${1:-}" in
  pull) pull_plans ;;
  push) push_plans ;;
  sync)
    pull_plans
    push_plans
    ;;
  *) usage ;;
esac
