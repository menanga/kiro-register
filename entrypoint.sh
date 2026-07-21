#!/bin/bash
set -e

# Headless mode does not need a virtual display.
# If --no-headless is requested (visible browser), wrap the command with xvfb-run
# so Playwright can launch a headed Chromium inside a Docker container.
for arg in "$@"; do
    if [ "$arg" = "--no-headless" ]; then
        exec xvfb-run -a --server-args="-screen 0 1920x1080x24" "$@"
    fi
done

exec "$@"
