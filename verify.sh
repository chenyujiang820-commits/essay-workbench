#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 一键验证链：后端 ruff -> mypy -> pytest(覆盖率>=80)；前端 tsc -> eslint -> vitest。
# 任何一级失败立即退出非零。用法：bash verify.sh
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"

resolve_python() {
  if [ -x "$BACKEND/.venv/Scripts/python.exe" ]; then
    echo "$BACKEND/.venv/Scripts/python.exe"
  elif [ -x "$BACKEND/.venv/bin/python" ]; then
    echo "$BACKEND/.venv/bin/python"
  else
    echo "python"
  fi
}

PY="$(resolve_python)"

echo "============================================================"
echo "[1/6] backend: ruff"
echo "============================================================"
( cd "$BACKEND" && "$PY" -m ruff check app tests )

echo "============================================================"
echo "[2/6] backend: mypy"
echo "============================================================"
( cd "$BACKEND" && "$PY" -m mypy app )

echo "============================================================"
echo "[3/6] backend: pytest (coverage >= 80%)"
echo "============================================================"
( cd "$BACKEND" && "$PY" -m pytest --cov=app --cov-report=term-missing --cov-fail-under=80 )

if ! command -v npm >/dev/null 2>&1; then
  echo "!! 未检测到 npm，无法验证前端（tsc/eslint/vitest）" >&2
  exit 1
fi

echo "============================================================"
echo "[4/6] frontend: tsc --noEmit"
echo "============================================================"
( cd "$FRONTEND" && npm run typecheck )

echo "============================================================"
echo "[5/6] frontend: eslint"
echo "============================================================"
( cd "$FRONTEND" && npm run lint )

echo "============================================================"
echo "[6/6] frontend: vitest"
echo "============================================================"
( cd "$FRONTEND" && npm run test )

echo ""
echo "============================================================"
echo "ALL CHECKS PASSED"
echo "============================================================"
