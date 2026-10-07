FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl is only for container health checks. Every dependency ships prebuilt wheels for
# amd64 and arm64 (the EC2 instance is Graviton), so no compiler is needed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md alembic.ini ./
COPY apps ./apps
COPY packages ./packages
COPY prisma ./prisma
COPY scripts ./scripts

RUN pip install -e . \
    && useradd --system --uid 10001 --home-dir /app tradzlog \
    && install -d -o tradzlog -g tradzlog /app/var/uploads

USER tradzlog

EXPOSE 8000 8001

CMD ["uvicorn", "tradzlog_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
