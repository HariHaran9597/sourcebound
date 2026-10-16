FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --default-timeout=120 --retries=5 --extra-index-url https://download.pytorch.org/whl/cpu .
COPY --link migrations ./migrations
COPY alembic.ini ./
RUN useradd --create-home --uid 10001 sourcebound && mkdir -p /app/.local && chown -R sourcebound:sourcebound /app
USER sourcebound
EXPOSE 8000
CMD ["uvicorn", "sourcebound_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
