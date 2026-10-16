"""Prune expired answer-history snapshots for a self-hosted deployment."""

import argparse
import logging
import time
from datetime import UTC, datetime

from sqlalchemy import delete

from sourcebound_api.config import get_settings
from sourcebound_api.db import SessionLocal
from sourcebound_api.models import AnswerTrace, RateLimitBucket

logger = logging.getLogger("sourcebound.retention")


def prune_expired() -> int:
    with SessionLocal() as session:
        now = datetime.now(UTC)
        history_deleted = 0
        if get_settings().store_answer_traces:
            result = session.execute(delete(AnswerTrace).where(AnswerTrace.expires_at <= now))
            history_deleted = result.rowcount or 0
        rate_limits_deleted = session.execute(
            delete(RateLimitBucket).where(RateLimitBucket.expires_at <= now)
        ).rowcount or 0
        session.commit()
    logger.info(
        "Pruned %d expired answer history entries and %d rate limit buckets",
        history_deleted,
        rate_limits_deleted,
    )
    return history_deleted + rate_limits_deleted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loop", action="store_true", help="Run continuously in a maintenance container.")
    parser.add_argument("--interval", type=int, default=3600, help="Seconds between cleanup passes.")
    args = parser.parse_args()
    if args.interval < 60:
        parser.error("--interval must be at least 60 seconds")
    while True:
        try:
            prune_expired()
        except Exception:
            logger.exception("Answer history retention pass failed")
        if not args.loop:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
