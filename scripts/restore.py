"""Restore a Sourcebound backup. Requires an explicit destructive-operation confirmation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUP_ROOT = ROOT / "backups"
START_ORDER = ("qdrant", "rabbitmq", "api", "worker", "retention", "frontend")
QUIESCE = ("api", "worker", "retention", "frontend", "qdrant", "rabbitmq")
BACKUP_ID_PATTERN = re.compile(r"\A\d{8}T\d{6}Z\Z")


def compose(*args: str, stdin=None, stdout=None) -> subprocess.CompletedProcess:
    command = ["docker", "compose"]
    env_file = ROOT / ".env"
    if env_file.is_file():
        command.extend(("--env-file", str(env_file)))
    command.extend(args)
    return subprocess.run(command, cwd=ROOT, check=True, stdin=stdin, stdout=stdout)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup_id", help="Timestamp ID printed by scripts/backup.py")
    parser.add_argument("--confirm", required=True, help="Must exactly equal RESTORE-<backup_id>")
    args = parser.parse_args()
    if not BACKUP_ID_PATTERN.fullmatch(args.backup_id):
        parser.error("backup_id must have the form YYYYMMDDTHHMMSSZ")
    if args.confirm != f"RESTORE-{args.backup_id}":
        parser.error(f"This replaces current database and data volumes. Pass --confirm RESTORE-{args.backup_id} to proceed.")

    source = BACKUP_ROOT / args.backup_id
    manifest_path = source / "manifest.json"
    database_dump = source / "postgres.dump"
    volume_archives = {
        "source_files.tar.gz": "/source_files",
        "qdrant_data.tar.gz": "/qdrant_data",
        "rabbitmq_data.tar.gz": "/rabbitmq_data",
    }
    artifacts = [database_dump, *(source / name for name in volume_archives)]
    if not manifest_path.is_file() or not all(path.is_file() for path in artifacts):
        print(f"Backup is incomplete: {source}", file=sys.stderr)
        return 2
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not read backup manifest: {exc}", file=sys.stderr)
        return 2
    if not isinstance(manifest, dict) or manifest.get("format_version") != 1 or manifest.get("backup_id") != args.backup_id:
        print("Unsupported or mismatched backup manifest.", file=sys.stderr)
        return 2
    hashes = manifest.get("sha256")
    expected_volumes = {"source_files", "qdrant_data", "rabbitmq_data"}
    included_volumes = manifest.get("included_volumes")
    if (
        not isinstance(hashes, dict)
        or not isinstance(included_volumes, list)
        or set(included_volumes) != expected_volumes
    ):
        print("Backup manifest is missing required checksums or volumes.", file=sys.stderr)
        return 2
    for artifact in artifacts:
        if hashes.get(artifact.name) != sha256(artifact):
            print(f"Checksum mismatch: {artifact.name}", file=sys.stderr)
            return 2

    try:
        running = compose("ps", "--services", "--status", "running", stdout=subprocess.PIPE).stdout.decode().split()
        if "postgres" not in running:
            print("Start the Sourcebound Compose stack before restoring.", file=sys.stderr)
            return 2
        stopped = [service for service in QUIESCE if service in running]
        if stopped:
            compose("stop", *stopped)

        with database_dump.open("rb") as source_dump:
            compose(
                "exec", "-T", "postgres", "sh", "-ec",
                'pg_restore --clean --if-exists --no-owner --no-privileges --single-transaction -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
                stdin=source_dump,
            )

        restore_commands = " ".join(
            f"tar -xzf /backups/{args.backup_id}/{filename} -C {volume};"
            for filename, volume in volume_archives.items()
        )
        restore_command = (
            f"find /source_files -mindepth 1 -maxdepth 1 -exec rm -rf -- {{}} +; "
            f"find /qdrant_data -mindepth 1 -maxdepth 1 -exec rm -rf -- {{}} +; "
            f"find /rabbitmq_data -mindepth 1 -maxdepth 1 -exec rm -rf -- {{}} +; "
            f"{restore_commands}"
        )
        compose("run", "--rm", "--no-deps", "backup-tools", "sh", "-euc", restore_command)
        to_start = [service for service in START_ORDER if service in stopped]
        if to_start:
            compose("start", *to_start)
        print(f"Restore completed from {source}. Verify service health and document retrieval before reopening access.")
        return 0
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Restore failed: {exc}", file=sys.stderr)
        print("Services were left stopped if the failure occurred after quiescing; inspect state before restarting.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
