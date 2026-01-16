#!/bin/bash

set -e  # Exit on command failure
set -o pipefail

BACKEND_LOG="/home/ubuntu/labs/sowGenTemplate/ai-doc-genv2/backend.log"
FRONTEND_LOG="/home/ubuntu/labs/sowGenTemplate/ai-doc-genv2/frontend.log"

echo "Starting backend..."
cd /home/ubuntu/labs/sowGenTemplate/ai-doc-genv2/backend || exit 1
python3 -m uvicorn main:app --reload --host 0.0.0.0 --port 8001 > "$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!

echo "Starting frontend..."
cd /home/ubuntu/labs/sowGenTemplate/ai-doc-genv2/frontend || exit 1
python3 -m http.server 8080 --bind 0.0.0.0 > "$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!

echo "✅ Both frontend (8080) and backend (8001) are running."

# Trap SIGTERM to stop both
trap "kill $BACKEND_PID $FRONTEND_PID; exit 0" SIGTERM

# Wait for both, and exit cleanly only if both succeed
wait $BACKEND_PID
BACKEND_EXIT=$?

wait $FRONTEND_PID
FRONTEND_EXIT=$?

if [ "$BACKEND_EXIT" -ne 0 ] || [ "$FRONTEND_EXIT" -ne 0 ]; then
  echo "❌ One of the services exited with failure."
  exit 1
fi

