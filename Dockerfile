FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app --home /app app

COPY pyproject.toml ./
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY var/tmp_uploads/.gitkeep ./var/tmp_uploads/.gitkeep

RUN pip install --no-cache-dir . \
    && chown -R app:app /app/var

USER app

CMD ["python", "-m", "app.main"]
