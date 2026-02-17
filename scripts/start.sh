#!/bin/bash

set -e  # Exit on command failure
set -o pipefail

BASE_DIR="/home/ubuntu/labs/sowGenTemplate/ai-doc-genv2"
BACKEND_LOG="$BASE_DIR/backend.log"
APP_BACKEND_LOG="$BASE_DIR/app-backend.log"
FRONTEND_LOG="$BASE_DIR/frontend.log"
BACKEND_PID_FILE="$BASE_DIR/backend.pid"
APP_BACKEND_PID_FILE="$BASE_DIR/app-backend.pid"
FRONTEND_PID_FILE="$BASE_DIR/frontend.pid"

# Clean up stale PID files from previous runs.
rm -f "$BACKEND_PID_FILE" "$APP_BACKEND_PID_FILE" "$FRONTEND_PID_FILE"

echo "Starting backend..."
cd "$BASE_DIR/backend" || exit 1
python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8000 > "$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!
echo "$BACKEND_PID" > "$BACKEND_PID_FILE"

echo "Starting app backend..."
cd "$BASE_DIR" || exit 1
python3 -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8001 > "$APP_BACKEND_LOG" 2>&1 &
APP_BACKEND_PID=$!
echo "$APP_BACKEND_PID" > "$APP_BACKEND_PID_FILE"

echo "Starting frontend..."
cd "$BASE_DIR/frontend" || exit 1
python3 -m http.server 8080 --bind 0.0.0.0 > "$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!
echo "$FRONTEND_PID" > "$FRONTEND_PID_FILE"

echo "✅ Frontend (8080), backend (8000), and app backend (8001) are running."

# Trap termination to stop all services and clean PID files.
trap 'kill "$BACKEND_PID" "$APP_BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true; rm -f "$BACKEND_PID_FILE" "$APP_BACKEND_PID_FILE" "$FRONTEND_PID_FILE"; exit 0' SIGTERM SIGINT

# Wait for all, and exit cleanly only if all succeed.
wait "$BACKEND_PID"
BACKEND_EXIT=$?

wait "$APP_BACKEND_PID"
APP_BACKEND_EXIT=$?

wait "$FRONTEND_PID"
FRONTEND_EXIT=$?

rm -f "$BACKEND_PID_FILE" "$APP_BACKEND_PID_FILE" "$FRONTEND_PID_FILE"

if [ "$BACKEND_EXIT" -ne 0 ] || [ "$APP_BACKEND_EXIT" -ne 0 ] || [ "$FRONTEND_EXIT" -ne 0 ]; then
  echo "❌ One of the services exited with failure."
  exit 1
fi
