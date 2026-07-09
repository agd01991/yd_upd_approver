from pathlib import Path

COMPOSE = Path("docker-compose.yml").read_text()


def service_block(name: str) -> str:
    lines = COMPOSE.splitlines()
    marker = f"  {name}:"
    start = next(index for index, line in enumerate(lines) if line == marker)
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if line.startswith("  ") and not line.startswith("    "):
            break
        block.append(line)
    return "\n".join(block)


def test_compose_runs_migrations_once_before_api_and_bot() -> None:
    migrate = service_block("migrate")
    api = service_block("api")
    bot = service_block("bot")

    assert "command: alembic upgrade head" in migrate
    assert "alembic upgrade head" not in api
    assert "alembic upgrade head" not in bot
    assert "migrate:" in api
    assert "condition: service_completed_successfully" in api
    assert "migrate:" in bot
    assert "condition: service_completed_successfully" in bot


def test_compose_waits_for_postgres_health_before_migrations() -> None:
    postgres = service_block("postgres")
    migrate = service_block("migrate")

    assert "healthcheck:" in postgres
    assert "pg_isready -U bot -d bot" in postgres
    assert "postgres:" in migrate
    assert "condition: service_healthy" in migrate
