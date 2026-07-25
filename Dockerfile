# Build context is the repo root so every service shares one image base.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY services/provider-gateway/provider_gateway ./services/provider-gateway/provider_gateway

# Installs the gateway package plus the shared runtime deps (fastapi, uvicorn, httpx, pydantic).
RUN pip install --no-cache-dir .

# Contracts shared by the hospital node and the portal.
COPY shared ./shared
ENV PYTHONPATH=/app

FROM base AS gateway

RUN mkdir -p /var/lib/provider-gateway

EXPOSE 8101

CMD ["provider-gateway"]

FROM base AS hospital-node

RUN pip install --no-cache-dir "PyJWT>=2.8.0"

COPY services/hospital-node/ ./services/hospital-node/
COPY data/hospitals/*.json ./data/hospitals/

ENV HOSPITAL_DATA_DIR=/app/data/hospitals

EXPOSE 8001

CMD ["python", "-m", "uvicorn", "main:app", \
     "--app-dir", "/app/services/hospital-node", \
     "--host", "0.0.0.0", "--port", "8001"]

FROM base AS portal

RUN pip install --no-cache-dir "PyJWT>=2.8.0"

COPY services/portal/portal ./services/portal/portal

ENV PYTHONPATH=/app:/app/services/portal

EXPOSE 8010

CMD ["python", "-m", "uvicorn", "portal.app:app", "--host", "0.0.0.0", "--port", "8010"]

FROM base AS coordinator

RUN pip install --no-cache-dir \
    "flask>=3.0.0" \
    "flask-cors>=4.0.0" \
    "requests>=2.31.0" \
    "python-dotenv>=1.0.0"

COPY coordinator ./coordinator

WORKDIR /app/coordinator

EXPOSE 5001

CMD ["python", "run.py"]
