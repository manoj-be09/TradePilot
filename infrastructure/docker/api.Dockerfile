FROM python:3.12.14-slim
WORKDIR /app
COPY pyproject.toml requirements.lock README.md ./
COPY alembic.ini ./
COPY alembic ./alembic
COPY apps ./apps
COPY packages ./packages
COPY scripts ./scripts
RUN pip install --no-cache-dir --no-deps -r requirements.lock \
    && pip install --no-cache-dir --no-build-isolation --no-deps . \
    && pip check
EXPOSE 8000
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
