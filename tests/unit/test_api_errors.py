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

    @app.get("/http-error")
    async def http_error():
        raise HTTPException(403, "Admin access required", headers={"WWW-Authenticate": "Telegram"})

    @app.get("/validation/{item_id}")
    async def validation(item_id: int):
        return {"item_id": item_id}

    @app.get("/db")
    async def db():
        raise SQLAlchemyError("postgresql://bot:secret@localhost/db")

    @app.get("/boom")
    async def boom():
        raise RuntimeError("secret traceback token")

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
    assert "secret traceback token" not in str(body)
    assert body["request_id"] in caplog.text
    assert "Authorization" not in caplog.text
    assert "bad=secret" not in caplog.text


def test_database_error_is_safe():
    client = TestClient(build_app(), raise_server_exceptions=False)
    response = client.get("/db")
    body = assert_error_contract(response, "database_unavailable")
    assert response.status_code == 503
    assert "secret" not in str(body)
