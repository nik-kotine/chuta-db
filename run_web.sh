#!/usr/bin/env bash

set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

cleanup() {
	kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
	PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi

if ! "$PYTHON_BIN" -c "import uvicorn" >/dev/null 2>&1; then
	echo "Error: uvicorn no está instalado en $PYTHON_BIN" >&2
	exit 1
fi

echo "Backend: http://localhost:8000"
echo "Frontend: http://localhost:3000"

"$PYTHON_BIN" -m uvicorn web.backend.main:app --reload --port 8000 &
BACKEND_PID=$!

(cd web && npm run dev) &
FRONTEND_PID=$!

wait "$BACKEND_PID" "$FRONTEND_PID"
