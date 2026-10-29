# SMOS container image.
# Build:  docker compose build        (needs Docker Hub and PyPI; nothing else)
# The embedding model is bundled from ./models, so no Hugging Face access is needed
# at build or run time.
FROM python:3.12-slim

# Optional CPU-only PyTorch index (much smaller image). Empty = take torch from PyPI.
ARG TORCH_INDEX_URL=

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/smos/src

# Optional extra CA bundle (see certs/README.md) for networks that re-sign HTTPS traffic.
COPY certs /opt/smos/certs

# Dependencies first, so a code change does not re-download PyTorch.
COPY pyproject.toml ./
RUN if [ -f /opt/smos/certs/ca-bundle.crt ]; then export PIP_CERT=/opt/smos/certs/ca-bundle.crt; fi  && python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
 && if [ -n "$TORCH_INDEX_URL" ]; then pip install torch --index-url "$TORCH_INDEX_URL"; fi \
 && pip install -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

COPY README.md ./
COPY smos ./smos
RUN if [ -f /opt/smos/certs/ca-bundle.crt ]; then export PIP_CERT=/opt/smos/certs/ca-bundle.crt; fi  && pip install --no-deps .

# Bundled embedding model (Apache-2.0), loaded by path.
COPY models /opt/smos/models

# Fingerprint of the files that need a rebuilt image when they change; the in-app updater
# compares these with a release before it offers an update (docs/superpowers/specs/2026-10-28-self-update-design.md).
COPY pyproject.toml Dockerfile compose.yaml /opt/smos/build-files/
RUN python -m smos.update.manifest --out /opt/smos/build-info.json pyproject.toml=/opt/smos/build-files/pyproject.toml Dockerfile=/opt/smos/build-files/Dockerfile compose.yaml=/opt/smos/build-files/compose.yaml models=/opt/smos/models smos/launcher.py=/opt/smos/src/smos/launcher.py

RUN useradd --system --uid 10001 --create-home --home-dir /home/smos --shell /usr/sbin/nologin smos \
 && mkdir /data \
 && chown smos:smos /data

ENV SMOS_DATA_DIR=/data \
    SMOS_HOST=0.0.0.0 \
    SMOS_PORT=8765 \
    SMOS_USAGE_RETENTION_DAYS=90 \
    SMOS_EMBEDDING_MODEL_DIR=/opt/smos/models/all-MiniLM-L6-v2 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HOME=/home/smos

USER smos
WORKDIR /home/smos
VOLUME /data
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/v1/health' % os.environ.get('SMOS_PORT', '8765'), timeout=4)" || exit 1

CMD ["smos-server"]
