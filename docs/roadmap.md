# Build phases

## Phase 0 — Product definition (complete)

Set the product direction: an open-source, self-hostable enterprise knowledge copilot for changing knowledge bases. The signature promise is inspectable evidence and source-version history, with updates and conflicts made visible.

**Exit:** first release boundary and success measures are documented.

## Phase 1 — Engineering foundation (scaffolded)

Create the repository, React application, FastAPI service, local service definitions, product brief, and architecture notes.

**Exit:** frontend and API run independently; service boundaries and local setup are documented. Frontend production build, backend Python compilation, and SQLite migrations through revision 0011 are verified. The local Compose stack was also brought up on 2026-10-16; PostgreSQL reached revision 0011, service health checks passed, and API/frontend health endpoints returned successfully. The stack was validated locally only, not as a deployed production service.

## Phase 2 — Document ingestion (implementation complete for PDF v1)

Accept PDF uploads, validate file type and size, save original bytes, create stable document and immutable version records, parse documents asynchronously, and expose status in the UI.

**Exit:** an uploaded PDF reaches `ready` or `failed`; a user can see why; duplicate versions are detected; the original and parsed representation remain linked to the version record. A synthetic PDF upload and asynchronous indexing completed in the local Compose stack on 2026-10-16. Current limit: PDFs only, with no OCR quality review interface. Real-world format coverage and OCR behavior remain unvalidated.

## Phase 3 — Retrieval and grounded answers (implementation complete; validation pending)

Create source-addressable chunks, embeddings, hybrid search, reranking, answer generation, citations, and explicit abstention for unsupported questions. Implemented now: Docling page provenance, FastEmbed dense+SPLADE vectors, Qdrant, reciprocal-rank fusion, ACL-filtered candidate retrieval, an Apache-2.0 FastEmbed cross-encoder reranker with configurable candidate depth, an OpenAI-compatible local model adapter, citations linked to immutable version/chunk/page metadata, no-results abstention, rejection of uncited or unknown-citation answers, citation-number compaction, inline citation links to evidence cards, answer/history responses limited to cited passages, and a versioned-dataset evaluation runner for source/page/version retrieval, abstention, conflict suggestions, and optional forbidden-source/canary hits. Remaining: collect a real reviewed gold set, validate citation entailment and abstention behavior, and assess retrieval quality with a real corpus. Evaluation source/page metrics now describe cited evidence, not the full retrieval candidate pool.

**Checkpoint (2026-10-13):** Ran the existing five-case fictional Northstar evaluation against Groq `qwen/qwen3.8-27b`, with the reranker disabled. The runner reported no request errors; source/page/version recall and version precision were 1.0, abstention precision/recall/F1 were 1.0, source precision was 0.8333, page precision was 0.8, and observed latency was P50 658 ms / P95 1,006 ms. A manual answer review confirmed the five expected outcomes. The relationship case returned both the vendor guide and runbook, while its strict expected-source labels include only the vendor guide; this accounts for the extra citation in the precision score. These are smoke-test results on five synthetic cases, not a quality benchmark.

**Checkpoint (2026-10-16):** Re-ran five synthetic cases against the local Compose stack with Qwen 3.8 27B and the cross-encoder reranker enabled. There were no request errors; expected source recall was 1.0, source precision 0.8333, page recall 1.0, page precision 0.8, version recall/precision 1.0, and abstention F1 1.0. P50 latency was 1.48 s and P95 was 22.46 s, with one 22.46 s outlier. The report is local/ignored at `evals/reports/sourcebound-demo-v1.local.json`. Five synthetic cases do not establish answer correctness or quality; no conflict or forbidden-source labels were present in this dataset.

**Exit:** answer citations resolve to the exact document version and passage; quality is measured on a versioned question set. Implementation work is in place; validation on a reviewer-curated, authorized corpus remains open.

## Phase 4 — Knowledge change lineage (active)

Compare versions, display changed sections, preserve answer provenance, detect source conflicts, and add selective relationship-aware retrieval for questions that need it. Implemented now: immutable uploaded versions, index switches only after the new version parses and indexes, latest-two parsed text diff API/UI, temporal-intent retrieval that adds older-version candidates under the same organization/document ACLs, admin-curated typed document links with audit events and access-filtered listing, relationship-intent retrieval expansion through links from the strongest authorized passages, visible and persisted retrieval scope, version-number metrics in the evaluation runner, user-scoped answer history with exact cited-passage snapshots, current-access rechecks/redaction, per-user history deletion and admin source deletion across files, vectors, and citing answer traces, configurable retention cleanup, and LLM-assisted potential conflict findings linked to two or more retrieved passages, preserved in answer history, and confirmable/dismissible by the answer owner with optional rationale and an audit event. Remaining: evaluate temporal and relationship expansion on reviewed corpora, validate conflict quality with reviewer-labeled cases, and exercise the history API and PostgreSQL migration path on a running stack. The SQLite migration chain through 0011 has been applied; PostgreSQL migration execution and history API behavior remain unverified.

**Checkpoint (2026-10-16):** The synthetic runbook revision comparison retrieved both versions and the relationship question retrieved the linked vendor guide. PostgreSQL migrations through revision 0011 ran in the local Compose stack. This confirms the walkthrough path only; quality, conflict detection, and latency benefits still need reviewer-labeled evaluation. History API access/redaction behavior still needs a focused verification pass.

**Exit:** a source update is visible and prior answers retain their original evidence; temporal and relationship retrieval have a measured quality and latency benefit on selected questions. PostgreSQL/Compose migrations are verified locally; the history API and reviewer-labeled quality evaluation remain open.

## Phase 5 — Identity and security controls (in progress)

Add user and group identity, document-level authorization, organization boundaries, audit events, secure secrets, upload limits, safe URL handling, and adversarial retrieval checks. Implemented now: optional OIDC Authorization Code + PKCE frontend login, backend RS256 discovery/JWKS verification, required organization-claim scoping, an admin-managed organization group catalog backfilled from existing document policies, exact-match group selection in document ACL controls, guardrails that prevent deleting groups still assigned to policies, audited group creation/deletion, bounded OIDC group claim counts/lengths, per-document group allow-lists, admin role checks, database plus Qdrant retrieval enforcement, answer-history access isolation, admin-readable audit events for uploads, version updates, access-policy changes, and source deletion, admin deletion that purges source files, vector points, and saved traces citing the source, baseline resource limits and kernel hardening on the PDF ingestion worker, per-user plus organization-wide PostgreSQL-coordinated request ceilings for Ask, ingestion, and reprocessing, transactionally accounted per-organization document/source-byte quotas visible to admins, and a synthetic restricted-document/canary demo plus evaluation support for forbidden-source citations and literal canary leakage. Remaining: page/derived-storage accounting, deployment-specific secret-manager examples, stronger parser isolation from network and shared data services, a real-provider OIDC integration check, and broader adversarial/security testing.

**Checkpoint (2026-10-13):** A two-case restricted-document evaluation ran against a temporary local OIDC issuer with separate admin and ordinary-user identities, after confirming the restricted document was indexed. The allowed case cited the incident runbook; the denied canary case abstained with no forbidden citation or canary-term hit. This is a synthetic control-flow check, not a real identity-provider integration or security certification. The setup exposed and fixed an access-group creation bug: flush the new group row before using its generated ID in the audit event.

**Exit:** access-control validation shows no unauthorized source or answer leakage; threat model and deployment boundaries are documented.

## Phase 6 — Production operation (in progress)

Add database migrations, durable worker deployment, health/readiness, retries and dead-letter handling, observability, backup/restore, rate limits, capacity limits, and upgrade procedures. Implemented now: Alembic schema migrations through revision 0011, Compose services for API/worker/PostgreSQL/Qdrant/RabbitMQ/frontend plus answer-history and rate-bucket retention, readiness checks, non-root API image, a resource-limited ingestion worker container, request ID and trace correlation, privacy-aware structured HTTP logs, Prometheus request metrics, opt-in OpenTelemetry spans from API request through Ask retrieval/generation and Celery PDF parse/index with W3C context propagation, an optional loopback-only Jaeger 2.22 profile, PostgreSQL document-version state gauges and persisted processing-attempt/latest-attempt duration quantiles, RabbitMQ pending/failed queue depth and consumer gauges, bounded exponential retries for transient indexing failures, a durable RabbitMQ dead-letter queue, admin reprocessing for failed document versions, quiesced backup plus guarded restore scripts for Postgres and persistent data volumes, starter Prometheus scrape/alert rules plus a Grafana dashboard, PostgreSQL-coordinated per-user and organization-wide request ceilings for expensive operations, transactionally serialized organization storage quotas, process-local API/Ask/ingestion concurrency ceilings with bounded queue wait and retry responses, and a documented single-host upgrade procedure. Remaining: live trace-export validation, backup/restore drills, and live upgrade rehearsals. Concurrency controls are per API replica; deployment-wide ceilings still belong at ingress.

**Checkpoint (2026-10-16):** The local Compose stack is healthy; API/frontend endpoints return successfully, PostgreSQL is at migration head 0011, Qdrant is reachable, and the Celery worker responds to ping. The synthetic upload-to-answer flow and five-case evaluation completed. A quiesced backup passed manifest hash checks and PostgreSQL archive inspection. The isolated restore drill returned three documents/four versions, restored four Qdrant points, and passed API readiness after database and broker credentials were aligned to the deployment `.env`. A restored-data Ask request returned the expected 15-minute notification answer with a citation to Northstar Incident Runbook v2, page 1. This proves the synthetic recovery path only. Celery's RabbitMQ 4.x configuration now uses exclusive control queues and disables optional gossip on the single-worker Compose deployment; the worker was rebuilt and pinged successfully. Live HTTP traces reached Jaeger, and an inbound W3C trace ID was preserved through an API request. Isolated checks confirmed a `429` Ask rate response and a `503` capacity response with `Retry-After` for concurrent requests. Remaining operational gates: validate Celery trace continuity; rehearse a schema/image upgrade and recovery; replace development defaults with deployment-managed secrets; verify monitoring and alerts under representative load. Concurrency controls are per API replica; deployment-wide ceilings still belong at ingress.

**Next gate:** validate Celery trace continuity and representative load behavior, then rehearse a schema/image upgrade and recovery before claiming operational readiness.

**Exit:** a reproducible deployment can be installed, operated, upgraded, recovered, and monitored from documented procedures. GitHub Actions repeat frontend lint/build, backend compilation, and SQLite migration-chain validation. PostgreSQL/Compose integration and a synthetic isolated restore are smoke-validated locally; production recovery and live upgrade rehearsals remain open.

## Phase 7 — Self-hosted release and pilot

Publish the open-source release, installation guide, evaluation report, security documentation, and issue/support workflow. A fictional four-PDF workflow corpus, five-case versioned evaluation walkthrough, and separate restricted-document/canary security fixture now exist for installation/demo practice. The five-case workflow was run locally on 2026-10-13 and manually reviewed; it is not a quality benchmark. The security scenario also passed against a temporary local OIDC issuer, but has not been validated with a real identity provider or a live Compose deployment. Still needed: pilot with an approved organization corpus, reviewer-labeled evaluation report, installation/recovery verification, and user feedback.

**Exit:** users outside the development environment can install and use the release; pilot outcomes and known limits are published.

## Phase 8 — Managed service

Add tenant provisioning and isolation, billing and quotas, managed connectors, model policies, service-level objectives, enterprise identity, and support operations. The staged architecture and migration gates are in `docs/managed-service-path.md`.

**Exit:** hosted service has independently validated tenant boundaries, operational runbooks, usage accounting, and customer support processes.



\n
