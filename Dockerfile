FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY . .
RUN pip install --no-cache-dir -e ".[dev]"
CMD ["sh", "-c", "alembic upgrade head && python -m app.main"]

