FROM python:3.11-slim

# Only the acme-retail demo tenant ships in the image, not the rest of
# user/ -- that directory also holds real local test tenants (personal
# uploads, real names) that must never reach a public deploy. A judge's own
# uploads through the running app still land under user/, and live only as
# long as the container: KPI_USER_ROOT is not set here.
WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
COPY src ./src
COPY templates ./templates
COPY schemas ./schemas
COPY deploy/user-metadata.yaml ./user/metadata.yaml
COPY user/acme-retail ./user/acme-retail

RUN uv sync --extra agent --extra cli --extra api --no-dev

ENV KPI_API_HOST=0.0.0.0

EXPOSE 8000

# Railway (and most PaaS hosts) inject PORT; __main__'s --port defaults from
# KPI_API_PORT, not PORT, so it is passed explicitly here.
CMD ["sh", "-c", "uv run python -m kpi_api --host 0.0.0.0 --port ${PORT:-8000}"]
