#!/bin/bash
# Launcher for kiro-register that handles headless environments

# Check if DISPLAY is set
if [ -z "$DISPLAY" ]; then
    echo "No DISPLAY detected - starting virtual display with Xvfb..."

    # Find an available display number
    DISPLAY_NUM=99
    while [ -e "/tmp/.X${DISPLAY_NUM}-lock" ]; do
        DISPLAY_NUM=$((DISPLAY_NUM + 1))
    done

    # Start Xvfb in background
    Xvfb :${DISPLAY_NUM} -screen 0 1920x1080x24 -nolisten tcp -nolisten unix &
    XVFB_PID=$!

    # Set DISPLAY for this session
    export DISPLAY=:${DISPLAY_NUM}

    # Wait a moment for Xvfb to start
    sleep 1

    echo "Virtual display started on :${DISPLAY_NUM}"

    # Cleanup function
    cleanup() {
        echo "Cleaning up Xvfb (PID: $XVFB_PID)..."
        kill $XVFB_PID 2>/dev/null
        exit
    }

    # Register cleanup on exit
    trap cleanup EXIT INT TERM
fi

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Run the application
python main.py "$@"
