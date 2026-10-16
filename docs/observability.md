# Operations and observability

## HTTP request signals

The API emits one JSON log record per completed request with a server-generated request ID, method, route template, status code, elapsed milliseconds, and the OpenTelemetry trace ID when tracing is enabled. It deliberately excludes request/response bodies, query strings, user subjects, document IDs, and bearer tokens. The request ID is returned in `X-Request-ID`. With tracing enabled, `X-Trace-ID` and Ask's `observability_trace_id` response field can be opened in Jaeger. Ask's separate `trace_id` remains the unique answer-history key and is safe even if an inbound caller reuses a W3C trace ID.

Prometheus counters and histograms are available at `GET /api/v1/metrics` when `SOURCEBOUND_METRICS_ENABLED=true`. The endpoint requires an OIDC workspace-admin role when OIDC is enabled. Protect the endpoint at the network layer as well; do not publish it through an untrusted reverse proxy. Metrics use route templates rather than resource IDs to keep label cardinality bounded.

Each scrape also refreshes shared operational gauges from PostgreSQL and, in Celery mode, RabbitMQ: `sourcebound_document_versions{status}` counts versions in queued/processing/indexing/ready/failed states; `sourcebound_document_processing_attempts{status}` sums persisted processing attempts by current version state; `sourcebound_document_processing_duration_seconds{status,quantile}` reports p50/p95 latest-attempt duration for up to the 500 most recently completed versions; `sourcebound_ingestion_queue_messages{queue}` reports pending and dead-letter queue depth; `sourcebound_ingestion_queue_consumers{queue}` reports active consumers; and `sourcebound_operational_dependency_up{dependency}` reports whether required dependencies could be queried. `sourcebound_document_processing_duration_samples{status}` reports the sample count for each duration quantile. `sourcebound_ingestion_queue_available{queue}` distinguishes an absent/uninspectable queue from an empty queue. Scrapes query aggregate status/attempt counts and inspect both queues; a broker connection attempt is limited to two seconds. A dependency outage leaves the endpoint scrapeable and marks that dependency down, but the associated state gauges should be treated as unavailable.

Readiness is reported at `/api/v1/health/ready` and checks the relational database and Qdrant connection; in Celery mode it also verifies RabbitMQ connectivity. Liveness at `/api/v1/health` reports only that the API process responds.

## Distributed traces

Tracing is opt-in. Set `SOURCEBOUND_TRACING_ENABLED=true` and start the Jaeger profile with `docker compose --profile observability up --build -d`. Open `http://127.0.0.1:16686`. The Jaeger UI and OTLP/HTTP receiver bind to loopback only; the API and worker send traces over the Compose network to `http://jaeger:4318/v1/traces`. For a host-run API, set `SOURCEBOUND_TRACE_OTLP_ENDPOINT=http://127.0.0.1:4318/v1/traces`. Root traces are sampled according to `SOURCEBOUND_TRACE_SAMPLE_RATIO` (default `1.0`); child spans follow their parent sampling decision. The Jaeger profile uses single-node in-memory storage and loses traces when restarted; use an organization-managed persistent trace backend for operational retention.

Trace trees include the HTTP request, primary and relationship-expanded retrieval, answer generation, task enqueue, and the background PDF parse/index stages. W3C trace context crosses the Celery task headers so API enqueue and worker processing share a trace. Propagation carries trace context only; baggage is disabled. Spans record route templates, status, stage result counts, page counts, and exception class names. They do not record questions, answers, document/version identifiers, source passages, user/organization IDs, exception messages, or stack traces. Treat the trace backend and UI as operationally sensitive, limit access, and set retention appropriate to the deployment.

## Document processing retries

Celery uses late acknowledgements and one-message worker prefetch. A worker lost during processing causes RabbitMQ to redeliver the task. Indexing failures are retried up to three times with exponential delays; deterministic parser/extraction failures are marked failed immediately. Rejected tasks route to the durable `document-ingestion.failed` queue through the `sourcebound.dead` exchange. Inspect it in RabbitMQ management at `http://127.0.0.1:15672`; after resolving the cause, a workspace admin can use **Retry parse** on the failed document. That action enqueues a fresh task and records an audit event. Inspect/acknowledge the old failed-queue message separately to prevent stale messages accumulating.

## Alerting starting points

Operators should establish workload-specific thresholds for 5xx rate, p95 request latency, queue depth/age, failed document processing, free disk, Postgres health, Qdrant health, and model endpoint errors. The repository does not set universal SLO values because latency and capacity depend on the chosen model, hardware, corpus, and deployment topology.

Example PromQL starting points (choose thresholds and `for` durations for the deployment):

```promql
sum(sourcebound_ingestion_queue_messages{queue="failed"}) > 0
```

```promql
sourcebound_ingestion_queue_messages{queue="pending"} > 100
```

```promql
sourcebound_ingestion_queue_messages{queue="pending"} > 0
and sourcebound_ingestion_queue_consumers{queue="pending"} == 0
```

## Packaged monitoring

The repository includes a Prometheus scrape configuration, a starter alert group, and a Grafana dashboard under `ops/`. The rule thresholds are operational defaults, not universal SLOs; review them against expected request volume and model latency before paging. The dashboard covers HTTP request rate and p95 latency, version states, queue depth/consumers, and dependency reachability.

Start Prometheus and Grafana using your organization's deployment mechanism. Mount `ops/prometheus/prometheus.yml` and `ops/prometheus/alerts.yml` into Prometheus; mount `ops/grafana/provisioning` at `/etc/grafana/provisioning` and `ops/grafana/sourcebound-dashboard.json` at `/var/lib/grafana/dashboards/sourcebound-dashboard.json`. Keep both services on a private operations network. Prometheus scrapes `/api/v1/metrics`; when OIDC is enabled, provide an appropriately scoped bearer token through Prometheus `authorization` configuration and a protected `bearer_token_file`, or use an authenticated internal metrics proxy. Never commit that token.

## Remaining work

This is a first observability layer, not a complete on-call system. Per-answer provenance, retention cleanup, bounded indexing retries, failed-task queue visibility, aggregate processing-state and attempt metrics, recent completed-task duration quantiles, backup/restore commands, starter alert/dashboard artifacts, per-API-process concurrency ceilings, and opt-in distributed traces are implemented. Configure `SOURCEBOUND_API_MAX_IN_FLIGHT`, `SOURCEBOUND_ASK_MAX_IN_FLIGHT`, `SOURCEBOUND_INGESTION_MAX_IN_FLIGHT`, and `SOURCEBOUND_CAPACITY_WAIT_SECONDS` for the available CPU, memory, model throughput, and database pool. Requests that cannot enter within the wait window receive `503` with `Retry-After`. These semaphores are process-local; multiply the configured ceiling by replica count when budgeting and enforce any deployment-wide cap at the ingress/load balancer. Local Compose validation has confirmed trace export to Jaeger and propagation of a caller-supplied W3C trace ID through the API. In the isolated restore deployment, a configured one-request Ask limit returned `429` on the next request; two concurrent Ask requests with a one-request in-flight limit returned one `503` with `Retry-After: 2` and one successful response. A synthetic isolated backup/restore drill has passed through a cited answer. Celery trace continuity, rate/capacity behavior under representative load, and a schema/image upgrade rehearsal still need live validation.
