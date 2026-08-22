FROM python:3.11-slim

WORKDIR /app

# System dependencies. HF Docker Spaces and attached writable volumes use
# UID 1000, so the application user must use that same numeric identity.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 --user-group appuser

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY --chown=appuser:appuser app ./app

# Bundled skills (e.g. Skills/anysearch-skill/). The deploy script
# copies the repo's Skills/ directory into the build context root
# alongside app/ + requirements.txt + Dockerfile. The backend's
# `anysearch_dir` setting resolves <WORKDIR>/Skills/anysearch-skill
# by default, which lands here.
COPY --chown=appuser:appuser Skills ./Skills

# Data + log dirs (HF Spaces can persist /data across rebuilds)
RUN mkdir -p /data/outputs /data/logs && \
    chown -R appuser:appuser /app /data

ENV HOME=/home/appuser \
    PATH=/home/appuser/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_PILOT_PORT=7860 \
    DATA_PILOT_HOST=0.0.0.0

EXPOSE 7860

USER appuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:7860/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1"]
