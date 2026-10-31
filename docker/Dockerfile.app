FROM mcr.microsoft.com/dotnet/runtime:8.0-noble

WORKDIR /app

# Environment
ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    LOG_LEVEL=INFO \
    PYTHON_EXE=python3

# Python and core dependencies
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    python-is-python3 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# App dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt --break-system-packages

# Copy source
COPY src /app/src
COPY libs /app/libs
COPY config.dist.toml /app/config.dist.toml

# Container entrypoint: auto-runs pending tasks when TASK_AUTO_RUN=true, else stays idle.
COPY docker/entrypoint.sh /app/entrypoint.sh

# Default entrypoint
ENTRYPOINT ["/bin/sh", "/app/entrypoint.sh"]
