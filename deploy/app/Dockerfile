# The Python application: API, worker, bank watch, delivery loop, mock ERP
# and the demo, one image with a different command each (docker-compose.yml).
# Versions are pinned; bump them deliberately.
FROM ghcr.io/astral-sh/uv:0.11.3 AS uv

FROM python:3.12.13-slim-bookworm
# Tesseract for scans; curl only to fetch its pinned language data below.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1
WORKDIR /app

# Dependencies first, so a code change does not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# The same language data as `make ocr-data` (tessdata_fast 4.1.0).
ARG TESSDATA_URL=https://github.com/tesseract-ocr/tessdata_fast/raw/4.1.0
RUN mkdir -p .tessdata \
 && for lang in deu eng; do curl -fsSL -o .tessdata/$lang.traineddata $TESSDATA_URL/$lang.traineddata; done

COPY . .
RUN uv sync --locked --no-dev

# Chromium for the browser connector (ERP_CHANNEL=form) adds several hundred
# MB; built in only when asked: --build-arg BROWSER=true.
ARG BROWSER=false
RUN if [ "$BROWSER" = "true" ]; then playwright install --with-deps chromium; fi

# The folders compose mounts volumes on exist here, owned by the app's user,
# so the volumes start out writable for it.
RUN useradd --system --create-home --uid 10001 einvoice \
 && mkdir -p data/mock_erp data/private data/demo \
 && chown -R einvoice data
USER einvoice
EXPOSE 8000 8100
CMD ["uvicorn", "--factory", "app.api.main:create_app_from_env", "--host", "0.0.0.0", "--port", "8000"]
