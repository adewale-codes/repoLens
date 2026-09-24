# RepoLens API image.
#
# Build context is the repo root, both locally (`docker build -f
# backend.Dockerfile .`) and on Railway, so every COPY path below is relative
# to the repo root. Getting that wrong only shows up as a failed build on
# Railway, not locally.
FROM python:3.11-slim

# git: ingestion shallow-clones repos with the git CLI (services/fetch.py), and
# python:3.11-slim doesn't include it. Without it, every ingest job fails at
# "fetching". Nothing else should be needed from apt. Every requirement
# resolves to a prebuilt manylinux wheel for CPython 3.11, with no source
# builds, and the ELF dependencies of their native libraries are only glibc,
# libstdc++, libgcc_s, and zlib, all present in python:3.11-slim. onnxruntime
# in particular needs no libgomp. (Checked from the wheel files; see README
# "Deploying the API to Railway" for the container run-test.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # All persistent state lives here: the SQLite index (chunks, embeddings,
    # import graph, ingest jobs), the repo clones, and the downloaded embedding
    # model. On Railway, attach a Volume at exactly this path. Without one,
    # every redeploy wipes every indexed repo. main.py warns at startup if it's
    # on Railway with no volume mounted.
    REPOLENS_DATA_DIR=/data

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY config.py main.py schemas.py ./
COPY services/ ./services/

# Runs as root on purpose: Railway volumes are mounted root-owned, and images
# running as a non-root UID hit permission errors writing to them (per
# Railway's volume docs).

EXPOSE 8000

# For local `docker run`. On Railway, railway.json's startCommand takes over;
# both go through a shell so $PORT expands.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
