# syntax=docker/dockerfile:1
FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

WORKDIR /app

# Install Python dependencies, Playwright Chromium, and the virtual X server
# needed to run a visible (non-headless) browser inside Docker.
COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb \
    && pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install chromium \
    && rm -rf /var/lib/apt/lists/*

# Copy project files.
COPY . .

# Persistent directories for runtime config/data.
RUN mkdir -p /data /config

# Make entrypoint executable (it auto-wraps --no-headless with xvfb-run).
RUN chmod +x /app/entrypoint.sh

ENV PYTHONUNBUFFERED=1 \
    APPDATA=/data \
    CONFIG_PATH=/config/kiro_config.json \
    DOMAINS_PATH=/config/domains.txt \
    DB_PATH=/data/accounts.db

VOLUME ["/data", "/config"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

ENTRYPOINT ["/app/entrypoint.sh"]

# Default: run headless, one account every 5 minutes.
# In Dokploy's Command field override with:
#   python service.py --service --delay 300 --no-headless
# The entrypoint will automatically prepend xvfb-run when --no-headless is present.
CMD ["python", "service.py", "--service", "--delay", "300", "--headless"]
