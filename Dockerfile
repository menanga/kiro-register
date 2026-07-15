# syntax=docker/dockerfile:1
FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

WORKDIR /app

# Install Python dependencies and ensure Chromium matches the Playwright version.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install chromium

# Copy project files.
COPY . .

# Persistent directories for runtime config/data.
RUN mkdir -p /data /config

ENV PYTHONUNBUFFERED=1 \
    APPDATA=/data \
    CONFIG_PATH=/config/kiro_config.json \
    DOMAINS_PATH=/config/domains.txt \
    DB_PATH=/data/accounts.db

VOLUME ["/data", "/config"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Default: run one headless account every 5 minutes. Override in Dokploy's Command field.
CMD ["python", "service.py", "--service", "--delay", "300", "--headless"]
