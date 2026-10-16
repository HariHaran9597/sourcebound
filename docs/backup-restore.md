# Backup and restore

The scripts back up one self-hosted Compose workspace as a coordinated recovery point:

- PostgreSQL custom-format dump, including document/version metadata, access policy, answer history, and audit events.
- `source_files`, containing original PDFs and parsed artifacts.
- `qdrant_data`, containing the hybrid search index.
- `rabbitmq_data`, containing queued and dead-lettered task messages.

The model cache is excluded because it can be downloaded again. Back up the deployment's `.env` and identity-provider configuration separately using a secret manager; do not copy secrets into the backup directory or source control.

## Create a backup

Run this from the repository root while the Compose stack is up and PostgreSQL is healthy:

```powershell
python scripts/backup.py
```

The script stops the API, frontend, ingestion worker, retention worker, Qdrant, and RabbitMQ to quiesce writes. It leaves PostgreSQL running for `pg_dump`, then archives the stopped data volumes. Previously running services are started again even if the backup fails. A timestamped folder appears in `backups/` with `postgres.dump`, three volume archives, and `manifest.json` checksums.

This causes temporary downtime. Copy completed backup folders to separate durable storage. The archives are not encrypted; treat them as sensitive because they contain source documents, parsed text, vector data, and broker state. The manifest hashes detect accidental corruption but do not authenticate a backup against tampering. Keep encrypted, access-controlled copies and protect the signing/credential configuration separately.

## Restore a backup

Restore overwrites the configured PostgreSQL database and the three persistent data volumes. First verify the selected backup and its deployment compatibility, preserve a copy of current data if needed, and ensure no users or workers are active. Run:

```powershell
python scripts/restore.py 20261013T120000Z --confirm RESTORE-20261013T120000Z
```

Replace the example timestamp with the exact backup ID printed by the backup command. The script validates artifact hashes and the backup format before stopping services. The confirmation token is mandatory. On success it starts previously running services; on a restore error, it leaves the dependent services stopped for inspection while PostgreSQL remains running.

After restore, check `/api/v1/health/ready`, inspect the RabbitMQ queues, and verify that a known document is retrievable with citations before reopening access. Restore with the same Sourcebound Compose configuration and compatible PostgreSQL, Qdrant, and RabbitMQ versions. For a changed version, first restore into a disposable environment and validate an upgrade path.

### Isolated Compose restore drill

`ops/compose.restore-drill.yaml` defines a disposable project with separate Postgres, Qdrant, RabbitMQ, and source-file volumes. It binds no host ports. Use the repository `.env` explicitly so Compose resolves the same database, broker, and model settings as the normal stack; invoking this alternate Compose file without `--env-file .env` selects its development defaults.

```powershell
docker compose --env-file .env -f ops/compose.restore-drill.yaml -p sourcebound-restore-drill up -d postgres qdrant rabbitmq
$env:COMPOSE_FILE = "ops/compose.restore-drill.yaml"
$env:COMPOSE_PROJECT_NAME = "sourcebound-restore-drill"
python scripts/restore.py 20261016T172955Z --confirm RESTORE-20261016T172955Z
Remove-Item Env:COMPOSE_FILE
Remove-Item Env:COMPOSE_PROJECT_NAME
docker compose --env-file .env -f ops/compose.restore-drill.yaml -p sourcebound-restore-drill up -d api
```

The 2026-10-16 local drill validated the backup hashes and PostgreSQL archive, restored the database and persistent volumes into the isolated project, and returned three documents/four versions from the restored API. API readiness passed after aligning the restored database and broker credentials to the deployment `.env`. A follow-up Ask request completed within the API's 90-second model timeout and returned a citation to the restored Northstar Incident Runbook, version 2, page 1, for the 15-minute notification window. This validates a synthetic restored-data path, not answer quality on a real corpus. Stop the drill containers while preserving the restored volumes with `docker compose --env-file .env -f ops/compose.restore-drill.yaml -p sourcebound-restore-drill down` (do not add `-v` unless you intend to delete the drill data).

## Recovery limits

These scripts are a recovery mechanism, not a scheduled backup system or a completed disaster-recovery program. Set backup frequency and retention based on the organization's recovery-point objective, transfer backups off the host, encrypt them at rest and in transit, and rehearse restore in an isolated environment. There is no built-in backup encryption, scheduler, remote object-storage target, or tamper-evident signature yet. The local drill also showed that a PostgreSQL volume dump does not by itself reconcile deployment credentials: preserve the matching `.env` in a secret manager and validate database and broker logins after restoring data.
