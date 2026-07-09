# K.I.R.O Register - Service Mode Docker Image
FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Create directory for config and database
RUN mkdir -p /data

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Volume for persistent data (config, database, domains)
VOLUME ["/data"]

# Default command (can be overridden)
CMD ["python", "service.py", "--help"]
