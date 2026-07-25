# Build context is the repo root so both services share one image base.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY services/provider-gateway/provider_gateway ./services/provider-gateway/provider_gateway

RUN pip install --no-cache-dir .

FROM base AS gateway

RUN mkdir -p /var/lib/provider-gateway

EXPOSE 8101

CMD ["provider-gateway"]

FROM base AS hospital-node

COPY services/hospital-node/ ./services/hospital-node/
COPY data/hospitals/*.json ./data/hospitals/
COPY auth ./auth
COPY shared ./shared
COPY search.py models.py ./

ENV HOSPITAL_DATA_DIR=/app/data/hospitals \
    PYTHONPATH=/app

EXPOSE 8001

CMD ["python", "-m", "uvicorn", "main:app", \
     "--app-dir", "/app/services/hospital-node", \
     "--host", "0.0.0.0", "--port", "8001"]
