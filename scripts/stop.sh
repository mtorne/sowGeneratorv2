#!/bin/bash

set -e

BASE_DIR="/home/ubuntu/labs/sowGenTemplate/ai-doc-genv2"

stop_service() {
    local name="$1"
    local pid_file="$2"

    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file")

        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
            echo "$name stopped (PID $pid)."
        else
            echo "$name PID file found, but process $pid is not running."
        fi

        rm -f "$pid_file"
    else
        echo "No $name process found."
    fi
}

stop_service "Backend" "$BASE_DIR/backend.pid"
stop_service "App backend" "$BASE_DIR/app-backend.pid"
stop_service "Frontend" "$BASE_DIR/frontend.pid"
