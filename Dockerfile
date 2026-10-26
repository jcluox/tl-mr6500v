FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app

# README.md and LICENSE are listed in pyproject.toml, so the package build needs them
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev

RUN useradd --uid 1000 --create-home app \
    && mkdir -p /app/data /app/logs \
    && chown -R app /app/data /app/logs
USER app

ENV PATH="/app/.venv/bin:$PATH" DATA_DIR=/app/data LOG_DIR=/app/logs
EXPOSE 8000
CMD ["uvicorn", "tl_mr6500v.main:app", "--host", "0.0.0.0", "--port", "8000"]
