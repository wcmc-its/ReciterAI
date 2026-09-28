FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt \
 && apt-get purge -y --auto-remove gcc

COPY . .

# Build id for utils.build_info.build_id() (#407) — TOPIC# rows carry it in
# `minted_by`. .git is dockerignored, so pass it in:
#   docker build --build-arg BUILD_SHA=$(git rev-parse --short HEAD) ...
# Absent, it is empty and build_id() reports "unknown".
ARG BUILD_SHA=
ENV RECITERAI_BUILD_SHA=$BUILD_SHA

RUN useradd --create-home --shell /bin/bash --uid 10001 reciterai \
 && chown -R reciterai:reciterai /app
USER reciterai

# Default command runs the daily enrichment job (#37 PR 4). The ECS task
# definition's scheduled invocation uses this default; on-demand
# `RunTask` calls override it via `containerOverrides[].command` —
#   - --from-gap-scan for the #112 onboarding-author backfill,
#   - --pmids <list> for ad-hoc sets,
#   - --full for an annual rescore.
CMD ["python", "-m", "scripts.run_daily_enrichment"]
