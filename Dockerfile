FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# install dependencies first so code changes do not invalidate this layer
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

EXPOSE 8501
# ingest is idempotent: it dedupes by transaction_id and replays the stream, so restarts are safe
CMD ["sh", "-c", "fraud-intel ingest --file data/transactions.csv.gz && streamlit run src/fraud_intel/dashboard/app.py --server.address=0.0.0.0 --server.port=8501 --server.headless=true"]
