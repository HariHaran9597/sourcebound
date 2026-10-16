# Self-hosted upgrade runbook

This is the operator procedure for a single-host Docker Compose installation. It is a documented procedure, not yet a rehearsed upgrade; run the first rehearsal on a disposable deployment before using it with important data.

## Before the maintenance window

1. Read the release notes and inspect migration revisions between the deployed and target versions. Confirm whether the change requires a maintenance window or has any irreversible data transformations.
2. Check available disk space for image builds and a full coordinated backup. Confirm PostgreSQL is healthy and record the currently deployed image/source revision.
3. Create a backup using [backup-restore.md](backup-restore.md). Copy the completed backup off-host before continuing. Keep the previous deployment revision available for a recovery decision.
4. Notify users and stop accepting uploads or questions during the upgrade window.

## Apply an upgrade

Run from the repository root with the intended release checked out and the deployment `.env` loaded by Docker Compose:

```powershell
docker compose ps
docker compose build api worker retention frontend
docker compose run --rm --no-deps api alembic upgrade head
docker compose up -d
docker compose ps
```

The explicit migration command makes schema changes visible before API traffic resumes. The API entrypoint also runs `alembic upgrade head` on startup as a safety net. Do not run two upgrades concurrently. Wait for health checks and inspect API, worker, retention, PostgreSQL, Qdrant, and RabbitMQ logs for migration, connectivity, or queue errors.

## Verify before reopening access

- Confirm `GET /api/v1/health/ready` succeeds through the deployment's configured endpoint.
- Confirm a known document is listed, an answer cites its expected source pages, and a non-member cannot access a restricted source in an OIDC-enabled deployment.
- Confirm a new small PDF can be ingested and reaches a terminal processing state; confirm admin usage counters reflect its source bytes.
- Inspect RabbitMQ ready/unacknowledged/dead-letter queue counts and check Prometheus targets and alerts.
- Reopen access only after these checks pass. Record the release revision, migration head, backup ID, and any deviations.

## Recovery decision

If the new release is unhealthy, keep user traffic closed. Prefer a forward fix when the migration is backward-compatible and data is intact. If reverting requires restoring prior schema or files, use the coordinated restore procedure in [backup-restore.md](backup-restore.md), which replaces the database and persistent volumes. Do not downgrade migrations or restore only PostgreSQL while leaving Qdrant/source files at the newer state unless the release explicitly documents that combination as compatible.

After any recovery, repeat the readiness, known-document retrieval, access-control, and queue checks before reopening the deployment.
