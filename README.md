# Sourcebound

An open-source enterprise knowledge copilot for changing knowledge bases. Sourcebound aims to answer questions across an organization's documents while making evidence, document versions, access decisions, and source relationships inspectable.

The project is licensed under [Apache License 2.0](LICENSE), a permissive license with an express contributor patent grant. Third-party models and packages keep their own licenses and terms.

## Current state

This repository is an active, pre-release build. The implemented path includes PDF upload and asynchronous indexing, immutable versions and diffs, dense+sparse hybrid search with optional reranking, cited answers with abstention, selective temporal retrieval, administrator-curated document relationships, potential-conflict review, OIDC with reusable organization groups and per-document group access, answer history, audit events, and a self-hosted Compose deployment with monitoring, migrations, backups, rate limits, and concurrency ceilings. On 2026-10-16, the local Compose stack completed a synthetic PDF ingestion and five-question RAG smoke evaluation against PostgreSQL, Qdrant, RabbitMQ, and the configured Groq Qwen endpoint. A synthetic isolated restore was also verified through a cited answer, and local trace, rate-limit, and overload responses were exercised. These are local checks, not a production deployment or quality benchmark. Reviewer-curated answer quality, real identity-provider integration, Celery trace continuity, production recovery, and upgrade/load rehearsals remain open. Do not use it with sensitive company data or expose it to the public internet yet.

GitHub Actions lints and builds the production frontend, compiles backend/migration sources, and applies the schema chain to a clean SQLite database. The local Compose smoke check additionally exercised PostgreSQL migrations, queue connectivity, ingestion, answer generation, and one synthetic isolated restore; it does not validate a real identity provider, production disaster recovery, or behavior at production scale.

## Product promise

> Ask questions across company knowledge, and see the evidence, version, and relationships behind each answer. When knowledge changes or sources conflict, make that visible.

## Planned architecture

- `frontend/`: React + TypeScript application
- `backend/`: FastAPI service and asynchronous ingestion workers
- PostgreSQL: organizations, identities, document metadata, versions, permissions, and audit events
- Qdrant: dense and sparse retrieval with permission filters
- Self-hosted storage: original source files and derived artifacts on a persistent volume (a production object-store adapter remains future work)
- Model adapters: hosted or local embedding, reranking, and generation endpoints

The first release is designed for one self-hosted organization. Organization identifiers and access boundaries are present in the data model, but the current deployment is not a hosted multi-tenant service.

The hosted-service migration gates, tenant-isolation strategy, control-plane boundary, and unresolved commercial/operational decisions are documented in [the managed-service path](docs/managed-service-path.md). The current build is not multi-tenant.

The first user workflow is **incident response and operational handoff for engineering/on-call teams**: find the current runbook step, verify its page and revision, review what changed, and hand off evidence. This focused workflow demonstrates Sourcebound's version-aware, permission-filtered evidence path while keeping the wider enterprise knowledge copilot direction. The Northstar demo corpus is synthetic and does not establish answer quality or production readiness. See [the pilot workflow and scorecard](docs/first-pilot-workflow.md).

## Run the frontend

```powershell
cd frontend
npm install
npm run dev
```

The Ask and Knowledge views connect to the local API. Authentication is disabled by default for local development; use only non-sensitive local test documents. OIDC can be enabled for a self-hosted deployment.

## Run the API

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
Copy-Item ..\.env.example .env
alembic upgrade head
uvicorn sourcebound_api.main:app --reload
```

API health: `http://127.0.0.1:8000/api/v1/health`

OpenAPI docs: `http://127.0.0.1:8000/docs`

The API defaults to SQLite and local file storage. Set `SOURCEBOUND_QDRANT_URL=http://127.0.0.1:6333` to use the Qdrant service instead of embedded local storage. The OpenAI-compatible model defaults to Ollama with `qwen3.5:2b`, a development choice rather than a quality benchmark or production model recommendation. Pull it from the [official Ollama Qwen 3.5 library](https://ollama.com/library/qwen3.5) before asking questions: `ollama pull qwen3.5:2b`. Its download size is not its full runtime-memory requirement. On an 8 GB Windows development host, Sourcebound parsed and indexed the demo runbook, but Ollama could not start this model while the local services were running; use a host with more available memory or a separately approved OpenAI-compatible model endpoint. Do not send company data to a remote model endpoint unless your organization has approved that data flow. The first indexing run downloads the configured FastEmbed dense and sparse models; the first query also downloads the reranker model. Run `alembic upgrade head` after pulling updates to apply schema migrations. For local development, PDF parsing runs as an API background task. To use the durable queue, start RabbitMQ, set `SOURCEBOUND_PARSE_MODE=celery`, and start a worker from the `backend` folder:

For Groq in Compose, set `SOURCEBOUND_LLM_BASE_URL=https://api.groq.com/openai/v1`, `SOURCEBOUND_LLM_API_KEY`, and `SOURCEBOUND_LLM_MODEL=qwen/qwen3.8-27b` in the ignored root `.env` file before starting the stack. Compose passes the key to both the API and ingestion worker. Never commit a real key; hosted inference sends each question and retrieved evidence to that provider.

```powershell
celery -A sourcebound_api.tasks.celery_app worker --loglevel=INFO
```

## Start local data services

Docker is required. Copy `.env.example` to `.env`, replace the local passwords, then run:

```powershell
docker compose up -d postgres qdrant rabbitmq
```

PostgreSQL, Qdrant, RabbitMQ, API, ingestion worker, and frontend are wired in Compose. The Compose profile uses Alembic migrations and the durable worker queue. Configure a local Ollama-compatible model endpoint; the app container connects to host Ollama via `host.docker.internal` by default. Start the self-hosted stack with:

```powershell
Copy-Item .env.example .env
# Change the local passwords before starting.
docker compose up --build -d
```

Open `http://127.0.0.1:5173`. These services bind to localhost by default. Compose is a self-hosted development/pilot starting point, not a hardened public deployment: configure TLS/reverse proxy, backups, monitoring, rate limits, and secret management before wider use. OIDC bearer validation and organization-claim scoping are available: set `SOURCEBOUND_AUTH_MODE=oidc`, issuer, audience, and client ID; configure the identity provider to include an `organization_id` claim and a top-level `sourcebound_admin` role for workspace admins; and set the matching frontend OIDC values before building. The frontend uses Authorization Code with PKCE. Workspace admins can register reusable exact-match group names and apply them to per-document access policies in the Knowledge view.

Ingestion workers retry transient indexing errors up to three times. Permanently failed tasks go to the durable `document-ingestion.failed` RabbitMQ queue; admins can schedule a fresh attempt for a failed version from the Knowledge view after resolving the cause. See the [processing operations notes](docs/observability.md) for failed-queue handling.

## Backup and restore

With the Compose stack running, create a coordinated backup using `python scripts/backup.py`. It briefly stops the API, frontend, workers, Qdrant, and RabbitMQ while it dumps PostgreSQL and archives the source, vector, and broker volumes. Backups are written under `backups/` and excluded from Git. Restore replaces the active database and data volumes; read [the recovery procedure](docs/backup-restore.md) and use the restore command's explicit confirmation before running it. Backups contain sensitive source data and are not encrypted by Sourcebound, so store them in access-controlled, encrypted storage.

Organization admins can view uploaded PDF source usage in the Knowledge view. Defaults are 1,000 documents and 10 GiB across retained PDF versions; configure `SOURCEBOUND_ORGANIZATION_MAX_DOCUMENTS` and `SOURCEBOUND_ORGANIZATION_MAX_STORAGE_BYTES` in `.env`. See [security controls](docs/security.md) for what the quota includes, the [recovery procedure](docs/backup-restore.md) for the completed synthetic isolated restore drill, and the [upgrade runbook](docs/upgrade-runbook.md) for the single-host deployment procedure. A live upgrade rehearsal and production recovery certification remain open.

## Retrieval behavior

Docling preserves page provenance. Source-addressable chunks are embedded with FastEmbed (`BAAI/bge-small-en-v1.5` plus SPLADE++) and stored in Qdrant with organization, document, immutable version, and page metadata. ACL-filtered dense and sparse candidates are merged using reciprocal-rank fusion, then reranked with FastEmbed's local cross-encoder (`Xenova/ms-marco-MiniLM-L-6-v2` by default). Set `SOURCEBOUND_RERANKER_ENABLED=false` for an ablation or to reduce CPU and first-request model-download costs. Answers use an OpenAI-compatible chat-completions endpoint (Ollama by default) and receive only the final reranked evidence. Citation IDs must map to that evidence; uncited or invalidly cited answers abstain, and the API returns only passages explicitly cited by the answer or potential-conflict findings. Inline answer citations open their corresponding evidence cards. Model-reported conflicts link to at least two passages, are preserved in answer history, and can be confirmed or dismissed by the answer owner with an optional rationale; they remain suggestions that require source review. Citation presence does not prove entailment. Source version updates keep older parsed files and vector points, switch search to the new successfully indexed version, and expose a unified text diff. Questions with explicit temporal or version-comparison wording also search older indexed versions; the answer records and shows that search scope. Workspace admins can curate same-organization document links; explicit relationship questions follow only those links that the caller may access, while linked metadata itself is not treated as answer evidence.

The initial retrieval implementation is not yet a quality guarantee. Reranking is implemented, and answers can surface model-generated potential conflicts with linked passages, but conflict quality has not been measured on a reviewed dataset. The demo includes an OIDC-gated ACL/canary red-team scenario, and the evaluation runner can record forbidden-source and literal-canary hits. A two-case synthetic check passed against a temporary local OIDC issuer; the scenario has not been validated with a real identity provider or a live Compose stack. There is no passage-entailment metric. Citation-ID validation ensures cited source numbers came from retrieval and that returned evidence is explicitly referenced; it does not prove support. Review findings against the source documents before acting on them.

## Try the fictional demo corpus

The repository includes a small fictional operations corpus, a versioned workflow question set, and a separate restricted-document security fixture in `evals/demo_corpus/`. Follow `evals/demo_corpus/README.md` to upload the incident runbook v1 and v2 as revisions of one document, upload the related guide and retention standard, create the documented admin-curated relationship, and run the evaluations. The corpus is for workflow demonstration only; it is not a benchmark and its expected answers are not quality claims.

## Evaluate an approved corpus

Prepare a reviewer-curated JSONL question set after uploading its source documents, then run the API evaluation tool:

```powershell
python scripts/evaluate_api.py evals/security-handbook.v1.jsonl --output evals/reports/security-handbook.v1.json
```

The output includes expected-source recall/precision, abstention metrics, optional reviewer-labeled conflict precision/recall/F1, optional forbidden-citation/canary hit rates, p50/p95 latency, a dataset hash, and model/retrieval/prompt versions. The repository contains the schema and instructions, not a fabricated score: use an approved corpus and review answer correctness, conflict findings, and passage entailment separately. See [evaluation instructions](evals/README.md).

## Observe requests

JSON request logs include a server-generated `X-Request-ID`, route template, status, elapsed time, and an OpenTelemetry trace ID when tracing is enabled; request and response bodies are not logged. Prometheus metrics are available at `/api/v1/metrics` and require a workspace-admin token in OIDC mode. They include HTTP latency, document processing states/attempts/duration quantiles, and RabbitMQ ingestion queue depth/consumer counts. Expensive actions have both per-user and cross-user organization-wide request-rate ceilings. Keep metrics and trace backends behind the internal network. See [observability notes](docs/observability.md) to enable the optional Jaeger profile and review what traces contain.

## Answer history and retention

Answer history is enabled by default and stores the question, answer, exact cited passage snapshots, and serving-version metadata in PostgreSQL for 30 days. Users can revisit and delete their own answers from the Ask view. The Compose `retention` service prunes expired entries hourly. Configure `SOURCEBOUND_STORE_ANSWER_TRACES=false` to disable storage, or set `SOURCEBOUND_ANSWER_TRACE_RETENTION_DAYS` to a value from 1 to 3650. Treat answer history as sensitive data because it can contain both user questions and excerpts from restricted documents; details are in [the security posture](docs/security.md).

Workspace admins can inspect recent organization-scoped security events in the Audit view or at `/api/v1/audit-events`. The event set covers document creation, version uploads, access-policy changes, source deletion, and conflict review dispositions. Admins can permanently delete a document and all versions; the API removes its stored files and Qdrant points, deletes answer-history traces that cite it, and retains an audit event. Deletion is blocked while a version is processing. This application audit trail is stored in the same database and is not tamper-proof against database administrators.

## Design references

The product is informed by real enterprise RAG workflows: large knowledge bases, source/version fidelity, cross-document relationships, mixed document formats, and operational trust. The initial milestone is an end-to-end path from uploading one document to opening a cited passage in an answer, followed by an update that preserves version history.



\n
