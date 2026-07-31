# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Builder — compiles wheels so the runtime image needs no toolchain.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

# PDF rendering is pure Python (xhtml2pdf), so no Pango/Cairo stack is
# needed. Locales are installed because invoices and reports must format
# French dates and numbers correctly.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        fonts-dejavu-core \
        gettext \
        locales \
        curl \
    && sed -i '/fr_FR.UTF-8/s/^# //g' /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/*

# Non-root: a compromised application process should not own its own code.
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home --shell /bin/bash app

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

COPY --chown=app:app . .

RUN mkdir -p /app/staticfiles /app/media \
    && chown -R app:app /app/staticfiles /app/media

USER app

# Compile translations and collect static at build time so the container
# starts fast and identically every time.
RUN python manage.py compilemessages --settings=config.settings.check 2>/dev/null || true
RUN DJANGO_SECRET_KEY=build-only \
    DATABASE_URL=sqlite:///build.sqlite3 \
    REDIS_URL=redis://localhost:6379/0 \
    python manage.py collectstatic --noinput --settings=config.settings.check \
    && rm -f build.sqlite3

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/health/ || exit 1

# 3 workers x 2 threads suits the stated 100-concurrent-user target on a
# modest VM. Raise workers, not threads, for CPU-bound growth.
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--threads", "2", \
     "--worker-class", "gthread", \
     "--timeout", "60", \
     "--graceful-timeout", "30", \
     "--max-requests", "1000", \
     "--max-requests-jitter", "100", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
