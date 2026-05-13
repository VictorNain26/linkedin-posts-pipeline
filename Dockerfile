# syntax=docker/dockerfile:1.7
# LinkedIn Posts Auto-Pipeline — multi-stage build (best practices mai 2026)
#
# Stages :
#   1. supercronic-builder : compile supercronic from source (Go récent)
#      → évite les CVE Go stdlib du binaire pré-compilé upstream
#   2. node-builder        : install node_modules (image officielle Node)
#      → npm récent, picomatch CVE fixed, npm retiré du runtime
#   3. python-builder      : compile les wheels Python
#   4. runtime             : image slim avec uniquement les artifacts finaux
#
# Sécurité :
# - Base Debian Trixie (chromium + Go stdlib à jour)
# - User non-root (UID 1000)
# - Pas de npm au runtime → 0 picomatch vulnérable
# - supercronic compilé from source → 0 CVE Go stdlib
# - HEALTHCHECK fonctionnel, dumb-init pour signals

# ============================================================
# Stage 1 — Supercronic builder (Go récent → pas de CVE stdlib)
# ============================================================
FROM golang:1.26-trixie AS supercronic-builder

ARG SUPERCRONIC_VERSION=v0.2.45

WORKDIR /build
RUN git clone --depth 1 --branch "${SUPERCRONIC_VERSION}" \
        https://github.com/aptible/supercronic.git . \
    && CGO_ENABLED=0 GOOS=linux go build \
        -ldflags="-s -w -X main.Version=${SUPERCRONIC_VERSION}" \
        -o /supercronic .

# ============================================================
# Stage 2 — Node builder (npm récent, image officielle)
# ============================================================
FROM node:22-trixie-slim AS node-builder

ENV PUPPETEER_SKIP_DOWNLOAD=true
WORKDIR /node-app
COPY package.json ./
RUN npm install --omit=optional --no-fund --no-audit

# ============================================================
# Stage 3 — Python builder
# ============================================================
FROM python:3.13.13-slim-trixie AS python-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# ============================================================
# Stage 4 — Runtime
# ============================================================
FROM python:3.13.13-slim-trixie AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Europe/Paris \
    LINKEDIN_DATA_DIR=/data \
    PIPELINE_DIR=/app \
    PYTHONPATH=/app \
    PATH="/home/linkedin/.local/bin:${PATH}" \
    PUPPETEER_SKIP_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

# ── Dépendances système ──
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        chromium \
        dumb-init \
        fonts-liberation \
        fonts-noto-color-emoji \
        libnss3 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libcups2 \
        libdrm2 \
        libxkbcommon0 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxrandr2 \
        libgbm1 \
        libpango-1.0-0 \
        libasound2 \
        sqlite3 \
        tzdata \
        util-linux \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# ── Node.js binaire depuis image officielle (pas de npm au runtime) ──
COPY --from=node-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=node-builder /usr/local/bin/corepack /usr/local/bin/corepack

# ── Supercronic compilé from source ──
COPY --from=supercronic-builder /supercronic /usr/local/bin/supercronic
RUN chmod +x /usr/local/bin/supercronic

# ── User non-root ──
RUN useradd --create-home --uid 1000 --shell /bin/bash linkedin \
    && mkdir -p /data /app \
    && chown -R linkedin:linkedin /data /app

USER linkedin
WORKDIR /app

# ── Python deps depuis builder ──
COPY --from=python-builder --chown=linkedin:linkedin /root/.local /home/linkedin/.local

# ── node_modules depuis node-builder (npm n'est PAS installé runtime) ──
COPY --from=node-builder --chown=linkedin:linkedin /node-app/node_modules /app/node_modules
COPY --chown=linkedin:linkedin package.json /app/package.json

# ── Code applicatif ──
COPY --chown=linkedin:linkedin . /app/
RUN cp /app/docker/crontab.docker /app/crontab

# ── Healthcheck ──
HEALTHCHECK --interval=5m --timeout=15s --start-period=30s --retries=2 \
    CMD python -c "import os, sys; \
        assert os.environ.get('ANTHROPIC_API_KEY'), 'ANTHROPIC_API_KEY missing'; \
        import config, history, format_selector, linkedin_post, linkedin_analytics, weekly_report; \
        history.init_db()" || exit 1

VOLUME ["/data"]

# ── Init system (dumb-init) → SIGTERM + zombie reaping ──
ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["supercronic", "-inotify", "/app/crontab"]
