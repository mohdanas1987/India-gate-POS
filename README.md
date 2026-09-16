# India Gate Smarter AI POS

New-build codebase (`main` branch) alongside the `reference-live-pos` branch, which is a frozen, as-is snapshot of the currently-running production POS — never developed on directly. See `docs/PHASE-STATUS.md` for exactly what in this repo is real/tested vs. scaffold-only, and what's still blocked on decisions or external information.

## Zero paid services during development

Everything here runs locally with no account, subscription, or paid API:
Postgres and Redis via Docker (`infrastructure/docker/docker-compose.yml`), a mock website integration provider (`integrations/website/mock.py`), and mock payment providers. Real providers are plugged in later, behind the same interfaces, only at UAT prep (Phase 21).

## Layout

```
apps/pos/desktop/     Electron + React + TS + Vite POS client
services/api/         FastAPI modular monolith (domain, auth, RBAC, website orders, sync)
integrations/website/ WebsiteIntegrationProvider interface + mock implementation
infrastructure/docker/ docker-compose for local Postgres/Redis
docs/                  Architecture, security, phase-status documentation
```

## Running the API locally

```
cd services/api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
docker compose -f ../../infrastructure/docker/docker-compose.yml up -d postgres redis
alembic upgrade head
uvicorn app.main:app --reload --port 8100
```

## Running the tests

```
cd services/api
export IGPOS_DATABASE_URL=postgresql+psycopg://igpos:igpos_dev_local@localhost:5432/igpos_test
export PYTHONPATH=".:../..:../../integrations"
python -m pytest tests/ -v
```

## Running the desktop app

```
cd apps/pos/desktop
npm install
npm run dev        # Vite dev server for the renderer
npm run typecheck  # renderer
npx tsc -p electron/tsconfig.json --noEmit   # electron main process
```

## Phase 0 forensic audit

The audit of the existing live POS (that informed every decision in this codebase) is in the project's saved docs, not duplicated here — see `docs/PHASE-STATUS.md` for a summary and pointers.
