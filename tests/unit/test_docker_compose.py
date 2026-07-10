from pathlib import Path

COMPOSE = Path("docker-compose.yml").read_text()
DEV_COMPOSE = Path("docker-compose.dev.yml").read_text()
DOCKERFILE = Path("Dockerfile").read_text()
DOCKERIGNORE = Path(".dockerignore").read_text().splitlines()


def service_block(content: str, name: str) -> str:
    lines = content.splitlines()
    marker = f"  {name}:"
    start = next(index for index, line in enumerate(lines) if line == marker)
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if line.startswith("  ") and not line.startswith("    "):
            break
        block.append(line)
    return "\n".join(block)


def main_service(name: str) -> str:
    return service_block(COMPOSE, name)


def dev_service(name: str) -> str:
    return service_block(DEV_COMPOSE, name)


def test_migrate_is_one_shot_and_only_service_running_alembic() -> None:
    migrate = main_service("migrate")
    api = main_service("api")
    bot = main_service("bot")

    assert "command: alembic upgrade head" in migrate
    assert 'restart: "no"' in migrate
    assert "alembic upgrade head" not in api
    assert "alembic upgrade head" not in bot
    assert "alembic upgrade head" not in DOCKERFILE.split("CMD", maxsplit=1)[-1]


def test_api_and_bot_wait_for_successful_migrations() -> None:
    for name in ("api", "bot"):
        block = main_service(name)
        assert "migrate:" in block
        assert "condition: service_completed_successfully" in block


def test_compose_waits_for_postgres_and_redis_health() -> None:
    migrate = main_service("migrate")
    assert "healthcheck:" in main_service("postgres")
    assert "pg_isready" in main_service("postgres")
    assert "healthcheck:" in main_service("redis")
    assert "redis-cli" in main_service("redis")
    assert "condition: service_healthy" in migrate


def test_main_compose_does_not_publish_postgres_or_redis() -> None:
    assert "ports:" not in main_service("postgres")
    assert "ports:" not in main_service("redis")
    assert "6379:6379" not in COMPOSE
    assert "5432:5432" not in COMPOSE


def test_dev_compose_publishes_only_loopback_ports() -> None:
    assert '"127.0.0.1:55432:5432"' in dev_service("postgres")
    assert '"127.0.0.1:8000:8000"' in dev_service("api")
    assert "redis:" not in DEV_COMPOSE


def test_postgres_password_is_not_hardcoded_in_compose() -> None:
    postgres = main_service("postgres")
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?" in postgres
    assert "POSTGRES_PASSWORD: bot" not in postgres


def test_dockerignore_excludes_env_and_temporary_uploads() -> None:
    assert ".env" in DOCKERIGNORE
    assert ".env.*" in DOCKERIGNORE
    assert "!.env.example" in DOCKERIGNORE
    assert "var/tmp_uploads" in DOCKERIGNORE
    assert "var/tmp_uploads/**" in DOCKERIGNORE


def test_dockerfile_copies_explicit_paths_and_uses_non_root_user() -> None:
    assert "COPY . ." not in DOCKERFILE
    assert "COPY app ./app" in DOCKERFILE
    assert "COPY alembic ./alembic" in DOCKERFILE
    assert "USER app" in DOCKERFILE
    assert 'CMD ["python", "-m", "app.main"]' in DOCKERFILE
