# LinkedIn Posts Auto-Pipeline — image Docker tout-en-un
# Stack : Python 3.13 (slim) + Node.js 22 + Chromium (Puppeteer) + supercronic (cron)
# Data persistante via volume sur /data
# Secrets via env vars ou fichier .env mounté

FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    LINKEDIN_DATA_DIR=/data \
    PIPELINE_DIR=/app \
    PYTHONPATH=/app

# ── 1. Dépendances système (Chromium pour Puppeteer + Node + fonts) ──
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        chromium \
        fonts-liberation \
        fonts-noto-color-emoji \
        fonts-noto-cjk \
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
        util-linux \
    && rm -rf /var/lib/apt/lists/*

# ── 2. Node.js 22 LTS ──
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── 3. supercronic (cron container-friendly, logge sur stdout) ──
# Note : pour un build prod hardened, pin SHA256 du binaire avec :
#   RUN echo "${SHA256} supercronic-linux-amd64" | sha256sum -c -
ENV SUPERCRONIC_VERSION=v0.2.45
RUN curl -fsSLO "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64" \
    && chmod +x supercronic-linux-amd64 \
    && mv supercronic-linux-amd64 /usr/local/bin/supercronic

# ── 4. Code + deps Python ──
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── 5. Deps Node (Puppeteer utilise le Chromium système, pas son bundle) ──
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium
RUN npm init -y && npm install puppeteer@latest --omit=optional

# ── 6. Code applicatif ──
COPY . .

# Crontab : voir crontab.docker (monté ou injecté à build)
COPY docker/crontab.docker /app/crontab

# Permissions exécutables
RUN chmod +x /app/pipeline.sh /app/healthcheck.sh /app/fetch_analytics.sh /app/weekly_report.sh

# ── 7. User non-root pour sécurité ──
RUN useradd -m -u 1000 linkedin && \
    mkdir -p /data && \
    chown -R linkedin:linkedin /app /data
USER linkedin

VOLUME ["/data"]

# Healthcheck (vérifie que le venv Python répond)
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import config, history; history.init_db()" || exit 1

# Entrypoint = supercronic qui lit /app/crontab et logge sur stdout
ENTRYPOINT ["supercronic", "-inotify"]
CMD ["/app/crontab"]
