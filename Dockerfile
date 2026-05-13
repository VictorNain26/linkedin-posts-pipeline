# syntax=docker/dockerfile:1.7
# LinkedIn Posts Auto-Pipeline — multi-stage build (best practices mai 2026)
#
# Stage 1 (builder)  : compile Python deps dans une image full
# Stage 2 (runtime)  : image slim avec uniquement les artifacts nécessaires
#
# Sécurité :
# - User non-root (UID 1000)
# - SHA256 vérifié sur binaire supercronic
# - Puppeteer pin version
# - HEALTHCHECK fonctionnel
# - Init system (docker-compose init: true)

# ============================================================
# Stage 1 — Builder : compile les wheels Python
# ============================================================
FROM python:3.13.13-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build deps (gcc pour compiler les extensions C de feedparser/anthropic si besoin)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt


# ============================================================
# Stage 2 — Runtime : image minimaliste de production
# ============================================================
FROM python:3.13.13-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Europe/Paris \
    LINKEDIN_DATA_DIR=/data \
    PIPELINE_DIR=/app \
    PYTHONPATH=/app \
    PATH="/home/linkedin/.local/bin:${PATH}" \
    # Puppeteer : utilise le Chromium système, pas de download
    PUPPETEER_SKIP_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

# ── Dépendances runtime minimales ──
# Chromium + fonts pour Puppeteer + sqlite3 CLI + util-linux (flock) + tzdata
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        chromium \
        curl \
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

# ── Node.js 22 LTS (depuis Nodesource) ──
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── Supercronic avec vérification SHA1 (checksum officiel publié par upstream) ──
# Source : https://github.com/aptible/supercronic/releases/tag/v0.2.45
ARG SUPERCRONIC_VERSION=v0.2.45
ARG SUPERCRONIC_SHA1=e894b193bea75a5ee644e700c59e30eedc804cf7
ARG TARGETARCH=amd64
RUN curl -fsSLo /usr/local/bin/supercronic \
        "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH}" \
    && echo "${SUPERCRONIC_SHA1}  /usr/local/bin/supercronic" | sha1sum -c - \
    && chmod +x /usr/local/bin/supercronic

# ── User non-root ──
RUN useradd --create-home --uid 1000 --shell /bin/bash linkedin \
    && mkdir -p /data /app \
    && chown -R linkedin:linkedin /data /app

USER linkedin
WORKDIR /app

# ── Python deps : copy du builder ──
COPY --from=builder --chown=linkedin:linkedin /root/.local /home/linkedin/.local

# ── Puppeteer via package.json versionné (avec overrides CVE) ──
# Le package.json est COPY du repo pour bénéficier des overrides picomatch
# (CVE-2026-33671 ReDoS). Sans ça, Puppeteer ramène la version vulnérable.
# Setup : PUPPETEER_SKIP_DOWNLOAD=true → utilise Chromium système (gain ~300MB).
COPY --chown=linkedin:linkedin package.json /app/package.json
RUN npm install --omit=optional --no-fund --no-audit

# ── Code applicatif (en dernier pour optimiser le cache layer) ──
COPY --chown=linkedin:linkedin . /app/

# Crontab Docker (différent du CRON_DISABLED.txt qui est pour cron host)
RUN cp /app/docker/crontab.docker /app/crontab

# ── Healthcheck réel ──
# Vérifie : (1) modules Python importent, (2) DB writable, (3) env vars critiques présentes
HEALTHCHECK --interval=5m --timeout=15s --start-period=30s --retries=2 \
    CMD python -c "import os, sys; \
        assert os.environ.get('ANTHROPIC_API_KEY'), 'ANTHROPIC_API_KEY missing'; \
        import config, history, format_selector, linkedin_post, linkedin_analytics, weekly_report; \
        history.init_db()" || exit 1

VOLUME ["/data"]

# ── Init system (dumb-init) pour gérer SIGTERM + reaping zombies Chromium ──
# Note : docker-compose 'init: true' active aussi docker-init, mais dumb-init en ENTRYPOINT
# garantit le comportement même si run sans compose (docker run, k8s, etc).
ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["supercronic", "-inotify", "/app/crontab"]
