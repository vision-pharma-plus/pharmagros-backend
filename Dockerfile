# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Builder — compiles wheels so the runtime image needs no toolchain.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Tolerate a slow or flaky connection. pip's default 15s read timeout
    # aborts the whole build when a multi-megabyte wheel (fonttools, pillow)
    # stalls mid-download, which is a network hiccup rather than a real
    # failure — so wait longer and retry instead of failing the image.
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# The cache mount keeps downloaded wheels between builds, so a retry after a
# dropped connection resumes from what already arrived rather than re-fetching
# every package. It lives in BuildKit's cache, not in the image, so nothing
# here inflates the final size.
#
# The explicit `id` is required: Railway's Metal builder rejects a cache mount
# without one ("missing an id argument"), where stock BuildKit defaults it to
# the target path. Naming it keeps the same cache on both.
RUN --mount=type=cache,id=pip-cache,target=/root/.cache/pip \
    pip wheel --wheel-dir /wheels -r requirements.txt


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

# WeasyPrint renders PDFs through Pango/Cairo, so those shared libraries are
# runtime dependencies, not build-time ones. Locales are installed because
# invoices and reports must format French dates and numbers correctly.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        shared-mime-info \
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

# Set explicitly rather than relying on the checkout: the repository is worked
# on from Windows, which does not carry a Unix executable bit.
RUN chmod +x /app/docker/entrypoint.sh

# /app/data is the mount point for the persistent volume that holds the SQLite
# database. It is created and owned here because the container runs as `app`:
# SQLite needs write access to the directory, not just the file, to create its
# -wal and -journal siblings.
RUN mkdir -p /app/staticfiles /app/media /app/data \
    && chown -R app:app /app/staticfiles /app/media /app/data

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
    CMD curl -fsS "http://localhost:${PORT:-8000}/health/" || exit 1

# The entrypoint migrates and seeds before starting gunicorn, and binds to
# $PORT so the container works on platforms that assign the port at run time.
ENTRYPOINT ["/app/docker/entrypoint.sh"]
