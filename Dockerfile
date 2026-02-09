# Multi-stage build for smaller image
FROM python:3.11-alpine AS BuildStage

WORKDIR /app

# Dependencies first (layer caching)
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Then application code
COPY src/ ./src/

# Final stage
FROM python:3.11-alpine

WORKDIR /app

# Binhex standard volumes
VOLUME ["/config", "/data"]

# Binhex standard environment variables with defaults
ENV PUID=99 \
    PGID=100 \
    UMASK=000 \
    TZ=UTC \
    DEBUG=false

# Copy application from build stage
COPY --from=BuildStage /app .

# Create standard directories
RUN mkdir -p /config /data

EXPOSE 9876

CMD ["python", "-m", "src.Main"]
