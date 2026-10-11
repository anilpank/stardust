# Lightweight container for the landing-page experiment (server + static files).
# The demo_signals.json it serves is generated OFFLINE by generate_demo_data.py,
# so this image needs only Flask + gunicorn — not the full stardust pipeline.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LANDING_DATA_DIR=/app/data \
    LANDING_COOKIE_SECURE=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY server.py index.html styles.css app.js demo_signals.json ./

# Writable location for signups.csv / events.csv (mount a volume in production).
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "-", "server:app"]
