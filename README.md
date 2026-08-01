# Vision Pharma Plus: Backend

Django 5.1 + DRF API for a wholesale pharmacy (*pharmacie de gros*) in Burundi:
batch-traceable inventory, credit sales, OBR-compliant invoicing, and bilingual
(FR/EN) documents.

The Next.js frontend lives in a separate repository and talks to this API over
REST/JSON with JWT auth.

- [Architecture](docs/architecture.md): design decisions and module layout
- [User guide](docs/user-guide.md)

---

## Requirements

| | Version | Notes |
|---|---|---|
| Python | 3.12 | Matches the Docker image and CI |
| PostgreSQL | 16 | Optional for local dev; SQLite is the default |
| Redis | 7 | Optional for local dev; Celery runs inline |

A clean checkout runs on SQLite with no external services. PostgreSQL and Redis
are only needed for the `dev`/`prod` profiles and for the integration tests.

---

## Quick start (local, SQLite)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt

python manage.py migrate
python manage.py seed_rbac       # roles and permission matrix
python manage.py seed_sequences  # gap-free document numbering
python manage.py seed_demo       # administrator account

python manage.py runserver
```

The API is then at <http://localhost:8000/api/v1/>.

`manage.py` defaults to `DJANGO_SETTINGS_MODULE=config.settings.local`, which
uses SQLite (`local.sqlite3`), an in-memory cache, eager Celery tasks, and a
console email backend. No `.env` file is required for this path.

### Demo credentials

`seed_demo` creates a single administrator and prints the login:

```
visionpharmaplus@gmail.com / Demo!2026#Pharma
```

Change this password before any real deployment. Re-running `seed_demo` is
idempotent; pass `--reset-passwords` to reset an existing account.

---

## Settings profiles

Select one with `DJANGO_SETTINGS_MODULE`.

| Module | Database | Cache / Celery | Use |
|---|---|---|---|
| `config.settings.local` | SQLite | locmem, eager | Default, zero setup |
| `config.settings.dev` | `DATABASE_URL` | Redis | PostgreSQL dev work |
| `config.settings.test` | `DATABASE_URL` | n/a | Used by pytest |
| `config.settings.prod` | `DATABASE_URL` | Redis | Deployment; strict security |

### Running against PostgreSQL

Copy the environment template and fill it in:

```bash
cp .env.example .env
```

At minimum set `DJANGO_SECRET_KEY`, `JWT_SIGNING_KEY`, `DATABASE_URL` and
`REDIS_URL`, then:

```bash
set DJANGO_SETTINGS_MODULE=config.settings.dev    # Windows cmd
# export DJANGO_SETTINGS_MODULE=config.settings.dev

python manage.py migrate
python manage.py runserver
```

`.env` is read by `django-environ` and is gitignored, so never commit it. Note the
comment in `.env.example` about bank-account currencies: write `USD`, not `$`,
because a leading `$` is parsed as a variable reference.

---

## Background workers

The `local` and `dev` profiles run Celery tasks inline
(`CELERY_TASK_ALWAYS_EAGER`), so no worker process is needed. To run them for
real, with Redis available:

```bash
celery -A config worker --loglevel=info
celery -A config beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

---

## API documentation

With the server running:

| URL | What |
|---|---|
| <http://localhost:8000/api/docs/> | Swagger UI |
| <http://localhost:8000/api/redoc/> | ReDoc |
| <http://localhost:8000/api/schema/> | OpenAPI 3 schema |
| <http://localhost:8000/admin/> | Django admin |
| <http://localhost:8000/health/> | Liveness: process is up |
| <http://localhost:8000/ready/> | Readiness: DB and cache reachable |

API modules are mounted under `/api/v1/`: `auth/`, `catalog/`, `partners/`,
`inventory/`, `sales/`, `invoicing/`, `purchasing/`, `reporting/`,
`notifications/`, `core/`.

### Getting a token

```bash
curl -X POST http://localhost:8000/api/v1/auth/login/ \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"visionpharmaplus@gmail.com\",\"password\":\"Demo!2026#Pharma\"}"
```

Send the returned access token as `Authorization: Bearer <token>` and refresh it
at `/api/v1/auth/refresh/`.

---

## Tests

```bash
pytest
```

`pytest.ini` selects `config.settings.test`. Three suites are excluded by
default (`test_end_to_end.py`, `test_api_smoke.py`, `test_translations.py`):
they need a live server or PostgreSQL. Run one explicitly to include it:

```bash
pytest tests/test_end_to_end.py
```

Tests marked `integration` require PostgreSQL; the FIFO locking, audit triggers
and CHECK constraints they exercise cannot run on SQLite.

### Lint and checks

Mirroring CI:

```bash
ruff check apps config
python manage.py check --deploy --fail-level WARNING
python manage.py makemigrations --check --dry-run
```

Ruff is configured in `pyproject.toml` and pinned to 0.16.1 in CI. The rule
set encodes the conventions the code already follows rather than stock
defaults, which fight Django idiom: `RUF012` would demand `ClassVar` on every
`Meta` and DRF attribute, and the star-import layering in `config/settings/`
is deliberate.

`ruff format --check` runs in CI but does not fail the build yet. The
formatter currently rewrites ~3300 lines across 51 files; until that lands as
its own commit, the gate stays advisory.

---

## Docker

`docker-compose.yml` runs the full production stack: PostgreSQL, Redis, the
backend, Celery worker and beat, nginx, and a nightly `pg_dump` backup service.

It expects a `.env` file with `POSTGRES_PASSWORD` and `REDIS_PASSWORD` set, and the
compose file fails fast if they are missing. Two variables point at the frontend
repository, which is consumed as a pre-built image rather than a build context:

- `FRONTEND_IMAGE`: a tag published from the frontend repo
- `NGINX_CONF_DIR`: path to that repo's `docker/nginx` directory

```bash
docker compose up -d --build
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py seed_rbac
docker compose exec backend python manage.py seed_sequences
```

To build the two together from source, clone the frontend beside this repo and
add a compose override replacing the `frontend` service with a `build:` context.

---

## Management commands

| Command | Purpose |
|---|---|
| `seed_rbac` | Roles and permission matrix. Additive unless `--prune` |
| `seed_sequences` | Initialise gap-free document number sequences |
| `seed_demo` | Administrator account only, no catalogue or transactions |
| `repair_plaintext_passwords` | One-off remediation for legacy password rows |

---

## Layout

```
apps/
  accounts/       users, roles, permissions, JWT auth, MFA
  catalog/        products, batches
  partners/       customers, suppliers
  inventory/      stock, movements, reservations
  sales/          orders, deliveries
  invoicing/      invoices, credit notes, PDF rendering
  purchasing/     purchase orders, receiving
  reporting/      exports and analytics
  notifications/  alerts
  core/           money, audit, health, shared services
config/
  settings/       base, local, dev, test, prod, check
  urls.py, celery.py, wsgi.py
tests/            pytest suites
docs/             architecture and user guide
```

Business rules live in each app's `services.py`, not in models or views. See
[docs/architecture.md](docs/architecture.md) for why.
