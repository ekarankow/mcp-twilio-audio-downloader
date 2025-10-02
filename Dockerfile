# Twilio Audio Downloader MCP Server Dockerfile
# Multi-stage build for optimized production image

# Build stage
FROM python:3.11-slim AS builder

# Set build arguments
ARG DEBIAN_FRONTEND=noninteractive

# Install system dependencies for building
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy dependency files and source code
COPY pyproject.toml ./
COPY src/ ./src/

# Create virtual environment and install Python dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

# Production stage
FROM python:3.11-slim as production

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/opt/venv/bin:$PATH"

# Set build arguments
ARG DEBIAN_FRONTEND=noninteractive

# Create non-root user for security
RUN groupadd --gid 1000 twilio && \
    useradd --uid 1000 --gid twilio --shell /bin/bash --create-home twilio

# Install runtime system dependencies
RUN apt-get update && apt-get install -y \
    # Network tools for debugging and health checks
    curl \
    netcat-openbsd \
    # Clean up
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy Python virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Set working directory
WORKDIR /app

# Copy application code and configuration files
COPY --chown=twilio:twilio src/ ./src/
COPY --chown=twilio:twilio pyproject.toml ./
COPY --chown=twilio:twilio run_server.py ./
COPY --chown=twilio:twilio config.example ./

# Create directories for logs and data
RUN mkdir -p /app/logs /app/data && \
    chown -R twilio:twilio /app

# Switch to non-root user
USER twilio

# Expose port
EXPOSE 8001

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD sh -c 'curl -f http://localhost:$TWILIO_PORT/health || exit 1'

# Default environment variables
ENV TWILIO_HOST=0.0.0.0
ENV TWILIO_PORT=8001
ENV TWILIO_LOG_LEVEL=INFO

# Default command
CMD ["sh", "-c", "python -m src.twilio_audio_downloader_mcp.server --host $TWILIO_HOST --port $TWILIO_PORT"]