FROM python:3.12-slim

WORKDIR /app

# Install deps before copying app source so the layer caches when only code changes.
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Copy app source. .dockerignore excludes everything we don't need.
COPY app ./app

ENV PORT=8080
EXPOSE 8080

# Single uvicorn worker: each WS pins state to one process; Cloud Run scales horizontally.
CMD exec uvicorn app.server:app --host 0.0.0.0 --port "$PORT" --workers 1 --no-access-log
