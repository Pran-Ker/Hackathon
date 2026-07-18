#!/bin/bash
# run-services.sh — bring up both microservices for the segment → 3D flow:
#   segment  (Stage 1, SAM 3)      → :8799  POST /api/stage1
#   pipeline (3D, Hunyuan3D)       → :8000  POST /imagine
# Ctrl+C stops both. See ARCHITECTURE.md.
set -e
cd "$(dirname "$0")"
SEG_PORT="${SEG_PORT:-8799}"
PIPE_PORT="${PIPE_PORT:-8000}"

SEG_PID=""; PIPE_PID=""
cleanup(){ echo; echo "stopping…"; [ -n "$SEG_PID" ] && kill "$SEG_PID" 2>/dev/null; [ -n "$PIPE_PID" ] && kill "$PIPE_PID" 2>/dev/null; }
trap cleanup EXIT INT TERM

# --- segment service (Python stdlib http + fal + GCS) ---
( cd segment
  [ -d .venv ] || python3 -m venv .venv
  ./.venv/bin/pip -q install -r requirements.txt
  [ -f .env ] || cp .env.example .env )
echo "segment  → http://localhost:$SEG_PORT   (POST /api/stage1)"
( cd segment && PORT="$SEG_PORT" ./.venv/bin/python server.py ) &
SEG_PID=$!

# --- pipeline service (FastAPI via uv) ---
( cd pipeline && [ -f .env ] || cp .env.example .env )
echo "pipeline → http://localhost:$PIPE_PORT   (POST /imagine)"
( cd pipeline && uv run uvicorn pipeline.server:app --host 0.0.0.0 --port "$PIPE_PORT" ) &
PIPE_PID=$!

echo "both up. Ctrl+C to stop."
wait
