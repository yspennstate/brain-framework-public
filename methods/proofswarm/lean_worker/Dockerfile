# Lean 4 + mathlib4 verification worker. FastAPI server exposes /verify.
# Uses the official elan installer. mathlib4 is pre-cached at build time so cold-starts are bearable.
FROM python:3.11-slim AS base

ENV DEBIAN_FRONTEND=noninteractive \
    LEAN_VERSION=v4.13.0 \
    PATH=/root/.elan/bin:/root/.cache/mathlib/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates git build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install elan (Lean version manager).
RUN curl -sSf https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
        | sh -s -- -y --default-toolchain ${LEAN_VERSION}

# Pre-fetch mathlib4 to warm the build cache.
WORKDIR /lean
COPY lakefile.lean lean-toolchain ./
# Stub source so `lake build` succeeds during image build.
RUN mkdir -p ProofSwarm && \
    echo "/-- placeholder --/" > ProofSwarm.lean && \
    lake update 2>&1 | tail -20 && \
    lake exe cache get 2>&1 | tail -5 || true

# Python deps for the FastAPI shim.
RUN pip install --no-cache-dir fastapi==0.115.0 uvicorn[standard]==0.32.0 pydantic==2.9.2

WORKDIR /app
COPY server.py /app/server.py

EXPOSE 8080
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
