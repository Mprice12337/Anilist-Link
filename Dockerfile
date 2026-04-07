# Multi-stage build for Anilist-Link
# Using slim-bookworm for Chromium compatibility (required for Crunchyroll client)

# ---- Build stage ----
FROM python:3.11-slim-bookworm AS buildstage

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
COPY --from=buildstage /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=buildstage /usr/local/bin /usr/local/bin

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

# Dev-only: stage database utility scripts at build time.
# They are copied to /config/dev-tools/ at container startup via entrypoint.
# Set BUILD_VARIANT=dev to include them (e.g. docker build --build-arg BUILD_VARIANT=dev)
ARG BUILD_VARIANT=release
ENV BUILD_VARIANT=${BUILD_VARIANT}
COPY scripts/dev-tools/ /tmp/dev-tools/
RUN if [ "$BUILD_VARIANT" = "dev" ]; then \
        mkdir -p /opt/dev-tools && \
        cp /tmp/dev-tools/*.sh /opt/dev-tools/ && \
        chmod +x /opt/dev-tools/*.sh && \
        apt-get update && apt-get install -y --no-install-recommends sqlite3 && \
        rm -rf /var/lib/apt/lists/*; \
    fi && \
    rm -rf /tmp/dev-tools/

COPY scripts/dev-tools/entrypoint.sh /opt/entrypoint.sh
RUN chmod +x /opt/entrypoint.sh

ENTRYPOINT ["/opt/entrypoint.sh"]
CMD ["python", "-m", "src.Main"]
