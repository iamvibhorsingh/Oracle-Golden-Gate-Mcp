# REST wrapper image. Mount credentials as secrets and use the *_FILE settings.
FROM python:3.12-slim

RUN useradd --system --create-home --uid 10001 ggmcp

WORKDIR /app
COPY pyproject.toml README.md MANIFEST.in ./
COPY src ./src
RUN pip install --no-cache-dir ".[azure,database]"

RUN mkdir -p /var/lib/ggmcp/logs /var/lib/ggmcp/data /var/lib/ggmcp/backups \
    && chown -R ggmcp:ggmcp /var/lib/ggmcp

ENV GG_AUDIT_LOG_PATH=/var/lib/ggmcp/logs/audit.log \
    GG_METRICS_DB_PATH=/var/lib/ggmcp/data/metrics.db \
    GG_BACKUP_DIR=/var/lib/ggmcp/backups \
    GG_LOG_FORMAT=json

VOLUME /var/lib/ggmcp
USER 10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/ready', timeout=3)"

CMD ["goldengate-rest-server", "--host", "0.0.0.0", "--port", "8000"]
