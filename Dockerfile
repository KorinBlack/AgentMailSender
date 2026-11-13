FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AMS_DATA_DIR=/data \
    AMS_HOST=0.0.0.0 \
    AMS_PORT=8080

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY agentmailsender ./agentmailsender
RUN pip install . \
    && useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin ams \
    && mkdir -p /data && chown ams:ams /data

USER ams
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"AMS_PORT\", \"8080\")}/healthz', timeout=4)"

CMD ["agentmailsender", "serve"]
