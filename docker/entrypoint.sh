#!/usr/bin/env bash
#
# Container entrypoint.
#
# Runs the schema migrations before the first worker accepts traffic, then
# hands off to gunicorn via exec so signals reach PID 1 and the platform can
# stop the container cleanly.
set -euo pipefail

# Railway (and most PaaS) assign the listening port at run time and route to
# it; a hardcoded 8000 would build fine and then fail every health check.
PORT="${PORT:-8000}"

echo "==> Applying migrations"
python manage.py migrate --no-input

# Idempotent: both commands report what already exists rather than duplicating
# it, so a restart or redeploy is safe.
echo "==> Seeding RBAC and sequences"
python manage.py seed_rbac
python manage.py seed_sequences

echo "==> Starting gunicorn on :${PORT}"
# 3 workers x 2 threads suits the stated 100-concurrent-user target on a
# modest VM. Raise workers, not threads, for CPU-bound growth.
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT}" \
    --workers "${GUNICORN_WORKERS:-3}" \
    --threads "${GUNICORN_THREADS:-2}" \
    --worker-class gthread \
    --timeout 60 \
    --graceful-timeout 30 \
    --max-requests 1000 \
    --max-requests-jitter 100 \
    --access-logfile - \
    --error-logfile -
