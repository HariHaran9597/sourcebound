from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from sourcebound_api.config import get_settings


class Base(DeclarativeBase):
    pass


def _engine_options(url: str) -> dict[str, object]:
    if url.startswith("sqlite"):
        if url.endswith("./.local/sourcebound.db"):
            Path(".local").mkdir(parents=True, exist_ok=True)
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


engine = create_engine(get_settings().database_url, **_engine_options(get_settings().database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
