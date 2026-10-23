# evidentia-backend

Agentic document-intelligence backend. A multi-agent extraction chain turns long, unstructured
documents into versioned, evidence-linked knowledge records; a bounded RAG engine answers
questions strictly from those records.

**Stack:** Python 3.13 · FastAPI · Pydantic v2 · SQLAlchemy 2 (async) · PostgreSQL 17 + pgvector
(HNSW) · Celery + Redis · S3-compatible storage · OpenAI via model-agnostic adapters · MCP ·
Prometheus · OpenTelemetry · uv · Ruff · mypy (strict) · pytest

## Architecture

```
ingest   signed upload → page text → agent chain → review gate → versioned record → chunk + embed → pgvector
                                     discover · classify · organise · translate          (HNSW, cosine)
                                     (code-validated between every hand-off)                    │
answer   question → scope → intent router → retrieve + rerank → evidence floor → 1 LLM call → localise
```

- **Models understand, code decides.** Merging, normalisation, validation, matching and grounding
  thresholds are deterministic, tested code. Agents only read, classify and write.
- **Strict contracts.** Every agent boundary is a Pydantic schema; malformed output raises typed
  errors. Retries cover transient failures only (1 s / 2 s / 4 s).
- **Two-layer prompts.** Instruction text resolves DB → env → default and is versioned; output
  contracts live in code.
- **Publishing.** SHA-256 content hash skips unchanged republishes; embedding runs in resumable
  batches; new versions swap in atomically with no gap.
- **Bounded agentic answering.** Deterministic planner, one generation call per question,
  injection-resistant prompt sections, and a full decision trace in diagnostic mode.
- **AIOps.** Every model call is persisted (tokens, latency, payload, retries, outcome) and exported
  as Prometheus metrics and OTLP spans with GenAI attributes; there is a built-in retrieval benchmark.
- **Agent access.** Core services are exposed as MCP tools (streamable HTTP, service-token gated).

## Run

```bash
cp .env.example .env
docker compose up --build        # api :8000 · worker · beat · postgres · redis · minio
docker compose run --rm api evidentia create-admin --email you@example.com
```

API docs: `http://localhost:8000/docs` · metrics: `/metrics` · probes: `/healthz`, `/readyz`.
`EVIDENTIA_LLM__PROVIDER=fake` runs everything offline with deterministic model adapters.

## Develop

```bash
uv sync && uv run pre-commit install
make lint typecheck test         # unit + offline end-to-end tests, no services needed
make test-all                    # adds integration tests on PostgreSQL/pgvector
make revision m="..." && make migrate
```

## Layout

```
src/evidentia/
  core/      config, logging, RFC 9457 errors, security, crypto, metrics, tracing, task dispatch
  db/        async engine, declarative base, model registry, migration hooks
  ai/        provider adapters, gateway, retries, structured output, prompts, telemetry
  modules/   feature modules (router · schemas · service · models)
  workers/   Celery app and background jobs
  agents/    MCP server
migrations/  Alembic (async)
tests/       unit · integration (TEST_DATABASE_URL) · offline end-to-end
```

All configuration is typed (`core/config.py`) and set through `EVIDENTIA_*` environment variables.
