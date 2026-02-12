# Multi-stage build for Anilist-Link
# Using slim-bookworm for Chromium compatibility (required for Crunchyroll client)

# ---- Build stage ----
FROM python:3.11-slim-bookworm AS BuildStage

WORKDIR /app

# Dependencies first (layer caching)
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# ---- Final stage ----
FROM python:3.11-slim-bookworm

WORKDIR /app

# Install Chromium and chromedriver for Crunchyroll Selenium auth
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium chromium-driver \
    fonts-liberation \
    libnss3 \
    libxss1 \
    libasound2 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from build stage
COPY --from=BuildStage /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=BuildStage /usr/local/bin /usr/local/bin

# Copy application code
COPY src/ ./src/
COPY pyproject.toml ./

# Binhex standard volumes
VOLUME ["/config", "/data"]

# Binhex standard environment variables with defaults
ENV PUID=99 \
    PGID=100 \
    UMASK=000 \
    TZ=UTC \
    DEBUG=false \
    CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver

# Create standard directories
RUN mkdir -p /config /data

EXPOSE 9876

CMD ["python", "-m", "src.Main"]
