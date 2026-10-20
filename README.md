# ai-training

Epic-01 Identity & Tenancy API (FastAPI, SQLAlchemy 2 async, PostgreSQL 16, Redis 7, arq).
Specs live in [specs/](specs/MANIFEST.md); the endpoint contract is
[specs/api-designer-spec.md](specs/api-designer-spec.md).

## Running locally

Prerequisites: Python 3.12+, [uv](https://docs.astral.sh/uv/), Docker.

```bash
uv sync
docker compose up -d                  # postgres:16 on :5432 and redis:7 on :6379 (user/db/pass: app)

export APP_DATABASE_URL=postgresql+asyncpg://app:app@localhost:5432/app
export APP_REDIS_URL=redis://localhost:6379/0
export APP_JWT_SECRET=<at least 32 random characters>
export APP_HASH_SALT=<at least 16 random characters>   # salt for IP / user-agent hashes

uv run alembic upgrade head                           # create the schema
uv run uvicorn app.main:create_app --factory --reload # API on http://localhost:8000
uv run arq app.jobs.worker.WorkerSettings             # background jobs (separate terminal)
```

* Interactive docs: <http://localhost:8000/docs>; schema: <http://localhost:8000/openapi.json>.
  Every route is under `/api/v1`; `GET /healthz` is the liveness probe.
* All settings are `APP_*` environment variables (see `app/core/config.py`). Optional ones:
  `APP_S3_BUCKET`, `APP_MEDIA_BASE_URL`, `APP_PUBLIC_BASE_URL` (frontend origin used in emails and
  share links) and `APP_RATE_LIMIT_OVERRIDES` (JSON, e.g. `{"login_ip": "100/minute"}`).
* Uploads (avatars, logos) use S3 presigned URLs, so they need AWS credentials and a real bucket.
  Emails are written to the log by the logging adapter; no SMTP is configured.

### Tests and quality gates

```bash
uv run pytest                # full suite; starts throwaway Postgres + Redis containers (Docker needed)
uv run pytest -m gate        # merge gates: cross-tenant 404 sweep, 20-way ShareLink redemption
uv run ruff check . && uv run ruff format --check . && uv run mypy .
```

`tests/test_openapi_contract.py` keeps the served OpenAPI document in line with
`specs/api-designer-spec.md` (routes, tags, status codes, error envelope).

### Load tests

k6 scenarios (one per spec target) and a 10 000-user seed live in `tests/load/`.
Use a throwaway database: the seed refuses to run twice but is not meant for shared data.

```bash
uv run alembic upgrade head
uv run python -m tests.load.seed_10k_users --dsn postgresql://app:app@localhost:5432/app

# the production login limit (10/min per IP) would 429 a load test, so raise it:
APP_RATE_LIMIT_OVERRIDES='{"login_ip":"1000000/minute","login_email":"1000000/minute","default_principal":"1000000/minute"}' \
  uv run uvicorn app.main:create_app --factory

k6 run tests/load/scenarios.js                       # all three, one after another
k6 run -e SCENARIO=me_read -e ME_RATE=50 tests/load/scenarios.js
```

| Scenario | Target | Env knobs |
|---|---|---|
| `me_read`: `GET /me` | p95 < 100 ms | `ME_RATE` (default 100 rps) |
| `login`: `POST /auth/login` | p95 < 300 ms at 50 rps | `LOGIN_RATE` (default 50 rps) |
| `admin_directory`: `GET /admin/users?limit=50[&query=]` | p95 < 500 ms at 10k users | `ADMIN_RATE` (default 20 rps) |

Common knobs: `BASE_URL`, `DURATION` (default `30s`), `SEEDED_USERS`, `SCENARIO`.
Login hashes with Argon2id (t=3, m=64 MiB, p=4), so the 50 rps target is CPU-bound: run the API
with several workers (`--workers N`) on hardware sized for it, and keep k6 off the same machine.
