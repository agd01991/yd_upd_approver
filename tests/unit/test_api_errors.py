import logging

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import (
    ApiError,
    api_error_handler,
    http_exception_handler,
    sqlalchemy_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.api.middleware import RequestIDMiddleware
from app.services.yandex_disk import YandexNetworkError


def build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException

    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api-error")
    async def api_error():
        raise ApiError(400, "file_too_large", "Файл превышает допустимый размер.", {"max": 1})

    @app.get("/api-error-5xx")
    async def api_error_5xx():
        raise ApiError(503, "upstream_failed", "Апстрим временно недоступен.")

    @app.get("/api-error-chained")
    async def api_error_chained():
        try:
            token = "Authorization: Bearer " + "CHAINSECRET"
            init_data = "initData=" + "CHAININIT"
            dsn = "postgresql://bot:" + "chainpass" + "@localhost/db"
            raise YandexNetworkError(f"timeout {token} {init_data} {dsn}")
        except YandexNetworkError as exc:
            raise ApiError(
                503,
                "yandex_disk_unavailable",
                "Яндекс.Диск временно недоступен.",
            ) from exc

    @app.get("/api-error-context")
    async def api_error_context():
        try:
            raise YandexNetworkError("implicit context failure")
        except YandexNetworkError:
            raise ApiError(503, "yandex_disk_unavailable", "Яндекс.Диск временно недоступен.")  # noqa: B904

    @app.get("/http-error")
    async def http_error():
        raise HTTPException(403, "Admin access required", headers={"WWW-Authenticate": "Telegram"})

    @app.get("/http-error-5xx")
    async def http_error_5xx():
        raise HTTPException(500, "upstream exploded")

    @app.get("/validation/{item_id}")
    async def validation(item_id: int):
        return {"item_id": item_id}

    @app.get("/db")
    async def db():
        dsn = "postgresql://bot:" + "secret" + "@localhost/db"
        auth = "Authorization: Bearer " + "DBSECRET"
        init_data = "initData=" + "DBINIT"
        raise SQLAlchemyError(f"{dsn} {auth} {init_data}")

    @app.get("/boom")
    async def boom():
        marker = "diagnostic traceback marker"
        auth = "Authorization: Bearer " + "SECRET"
        init_data = "initData=" + "bad=secret"
        dsn = "postgresql://bot:" + "secret" + "@localhost/db"
        raise RuntimeError(f"{marker} {auth} {init_data} {dsn}")

    return app


def assert_error_contract(response, code: str):
    body = response.json()
    assert set(body) == {"error", "request_id"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str)
    assert "X-Request-ID" in response.headers
    assert response.headers["X-Request-ID"] == body["request_id"]
    assert len(body["request_id"]) == 32
    int(body["request_id"], 16)
    return body


def test_api_error_contract():
    client = TestClient(build_app())
    response = client.get("/api-error")
    body = assert_error_contract(response, "file_too_large")
    assert response.status_code == 400
    assert body["error"]["details"] == {"max": 1}


def test_health_has_request_id():
    response = TestClient(build_app()).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert len(response.headers["X-Request-ID"]) == 32


def test_http_exception_and_404_contract():
    client = TestClient(build_app())
    response = client.get("/http-error")
    body = assert_error_contract(response, "admin_access_required")
    assert response.status_code == 403
    assert body["error"]["message"] == "Недостаточно прав администратора."
    assert response.headers["WWW-Authenticate"] == "Telegram"

    missing = client.get("/missing")
    assert missing.status_code == 404
    assert_error_contract(missing, "not_found")


def test_validation_details_are_safe():
    response = TestClient(build_app()).get("/validation/not-int?secret=telegram_init_data")
    body = assert_error_contract(response, "validation_error")
    assert response.status_code == 422
    assert body["error"]["details"]
    assert "input" not in str(body)
    assert "telegram_init_data" not in str(body)


def test_unhandled_exception_is_safe_and_logged(caplog):
    client = TestClient(build_app(), raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="app.api.errors"):
        response = client.get(
            "/boom",
            headers={"Authorization": "Bearer SECRET", "X-Telegram-Init-Data": "bad=secret"},
        )
    body = assert_error_contract(response, "internal_error")
    assert response.status_code == 500
    assert "diagnostic traceback marker" not in str(body)
    assert "Traceback (most recent call last)" not in str(body)
    assert body["request_id"] in caplog.text
    assert "Traceback (most recent call last)" in caplog.text
    assert "test_api_errors.py" in caplog.text
    assert "boom" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "NoneType: None" not in caplog.text
    assert "diagnostic traceback marker" in caplog.text
    assert "Authorization: Bearer SECRET" not in caplog.text
    assert "bad=secret" not in caplog.text
    assert "postgresql://bot:secret@localhost/db" not in caplog.text
    assert "Authorization: Bearer ***" in caplog.text
    assert "postgresql://bot:***@localhost/db" in caplog.text


def test_database_error_is_safe_and_logged(caplog):
    client = TestClient(build_app(), raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="app.api.errors"):
        response = client.get("/db")
    body = assert_error_contract(response, "database_unavailable")
    assert response.status_code == 503
    assert "secret" not in str(body)
    assert body["request_id"] in caplog.text
    assert "Traceback (most recent call last)" in caplog.text
    assert "test_api_errors.py" in caplog.text
    assert "db" in caplog.text
    assert "SQLAlchemyError" in caplog.text
    assert "NoneType: None" not in caplog.text
    assert "DBSECRET" not in caplog.text
    assert "DBINIT" not in caplog.text
    assert "postgresql://bot:secret@localhost/db" not in caplog.text


def test_handled_5xx_errors_log_real_traceback(caplog):
    client = TestClient(build_app(), raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="app.api.errors"):
        api_response = client.get("/api-error-5xx")
        http_response = client.get("/http-error-5xx")

    api_body = assert_error_contract(api_response, "upstream_failed")
    http_body = assert_error_contract(http_response, "http_error")
    assert api_response.status_code == 503
    assert http_response.status_code == 500
    assert "Traceback (most recent call last)" not in str(api_body)
    assert "Traceback (most recent call last)" not in str(http_body)
    assert api_body["request_id"] in caplog.text
    assert http_body["request_id"] in caplog.text
    assert "Traceback (most recent call last)" in caplog.text
    assert "api_error_5xx" in caplog.text
    assert "http_error_5xx" in caplog.text
    assert "ApiError" in caplog.text
    assert "HTTPException" in caplog.text
    assert "path=/api-error-5xx" in caplog.text
    assert "path=/http-error-5xx" in caplog.text
    assert "NoneType: None" not in caplog.text


def test_chained_api_error_preserves_safe_cause_traceback(caplog):
    client = TestClient(build_app(), raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="app.api.errors"):
        response = client.get(
            "/api-error-chained?initData=CHAININIT",
            headers={"Authorization": "Bearer CHAINSECRET", "X-Telegram-Init-Data": "CHAININIT"},
        )

    body = assert_error_contract(response, "yandex_disk_unavailable")
    assert response.status_code == 503
    response_text = str(body)
    assert "Traceback (most recent call last)" not in response_text
    assert "YandexNetworkError" not in response_text
    assert "timeout" not in response_text
    assert "CHAINSECRET" not in response_text
    assert "CHAININIT" not in response_text
    assert "chainpass" not in response_text
    assert body["error"]["details"] is None

    log_text = caplog.text
    assert body["request_id"] in log_text
    assert "path=/api-error-chained" in log_text
    assert "YandexNetworkError" in log_text
    assert "ApiError" in log_text
    assert "api_error_chained" in log_text
    assert "raise YandexNetworkError" in log_text
    assert "raise ApiError" in log_text
    assert "The above exception was the direct cause" in log_text
    assert "NoneType: None" not in log_text
    assert "Authorization: Bearer CHAINSECRET" not in log_text
    assert "CHAININIT" not in log_text
    assert "postgresql://bot:chainpass@localhost/db" not in log_text
    assert "Authorization: Bearer ***" in log_text
    assert "postgresql://bot:***@localhost/db" in log_text


def test_api_error_preserves_safe_context_traceback(caplog):
    client = TestClient(build_app(), raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="app.api.errors"):
        response = client.get("/api-error-context")

    body = assert_error_contract(response, "yandex_disk_unavailable")
    assert response.status_code == 503
    assert "YandexNetworkError" not in str(body)
    assert "implicit context failure" not in str(body)
    assert "YandexNetworkError" in caplog.text
    assert "ApiError" in caplog.text
    assert "api_error_context" in caplog.text
    assert "During handling of the above exception" in caplog.text
    assert "NoneType: None" not in caplog.text
