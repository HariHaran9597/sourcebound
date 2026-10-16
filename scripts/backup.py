"""Create a quiesced Sourcebound backup using Docker Compose and the standard library."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUP_ROOT = ROOT / "backups"
QUIESCE = ("api", "worker", "retention", "frontend", "qdrant", "rabbitmq")


def compose(*args: str, stdout=None) -> subprocess.CompletedProcess:
    command = ["docker", "compose"]
    env_file = ROOT / ".env"
    if env_file.is_file():
        command.extend(("--env-file", str(env_file)))
    command.extend(args)
    return subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        stdout=stdout,
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    backup_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = BACKUP_ROOT / backup_id
    destination.mkdir(parents=True, exist_ok=False)
    try:
        running = compose("ps", "--services", "--status", "running", stdout=subprocess.PIPE).stdout.decode().split()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Could not inspect the Compose stack: {exc}", file=sys.stderr)
        return 2
    if "postgres" not in running:
        print("Start the Sourcebound Compose stack before creating a backup.", file=sys.stderr)
        return 2
    stopped = [service for service in QUIESCE if service in running]

    backup_succeeded = False
    restart_failed = False
    try:
        if stopped:
            compose("stop", *stopped)

        database_dump = destination / "postgres.dump"
        with database_dump.open("wb") as output:
            compose(
                "exec", "-T", "postgres", "sh", "-ec",
                'pg_dump --format=custom --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
                stdout=output,
            )

        archives = {
            "source_files.tar.gz": "/source_files",
            "qdrant_data.tar.gz": "/qdrant_data",
            "rabbitmq_data.tar.gz": "/rabbitmq_data",
        }
        tar_commands = " ".join(
            f"tar -czf /backups/{backup_id}/{filename} -C {volume} .;"
            for filename, volume in archives.items()
        )
        tar_command = f"set -e; {tar_commands}"
        compose("run", "--rm", "--no-deps", "backup-tools", "sh", "-euc", tar_command)

        artifacts = (database_dump, *(destination / name for name in archives))
        manifest = {
            "format_version": 1,
            "backup_id": backup_id,
            "created_at": datetime.now(UTC).isoformat(),
            "database": "PostgreSQL custom-format dump",
            "included_volumes": [Path(volume).name for volume in archives.values()],
            "sha256": {path.name: sha256(path) for path in artifacts},
        }
        manifest_path = destination / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        try:
            for path in (*artifacts, manifest_path):
                os.chmod(path, 0o600)
            os.chmod(destination, 0o700)
        except OSError:
            pass
        print(f"Backup created: {destination}")
        print("Protect this directory as sensitive data and copy it to separate durable storage.")
        backup_succeeded = True
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Backup failed; partial files remain at {destination}: {exc}", file=sys.stderr)
        return 1
    finally:
        if stopped:
            try:
                compose("start", *stopped)
            except (OSError, subprocess.CalledProcessError) as exc:
                print(f"Could not restart services after backup: {exc}", file=sys.stderr)
                restart_failed = True
    return 0 if backup_succeeded and not restart_failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
