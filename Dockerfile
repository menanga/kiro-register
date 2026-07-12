# K.I.R.O Register - Docker Image
FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Create directories for config and data
RUN mkdir -p /data /config

# Environment variables (can be overridden)
ENV PYTHONUNBUFFERED=1 \
    CONFIG_PATH=/config/kiro_config.json \
    DOMAINS_PATH=/config/domains.txt \
    DB_PATH=/data/accounts.db

# Volume mounts for persistent data
VOLUME ["/data", "/config"]

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Default command
ENTRYPOINT ["python", "service.py"]
CMD ["--help"]
