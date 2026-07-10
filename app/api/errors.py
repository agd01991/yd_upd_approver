import logging
import uuid
from typing import Any

from fastapi import HTTPException as FastAPIHTTPException
from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging_config import redact_text

logger = logging.getLogger("app.api.errors")

DEFAULT_MESSAGES = {
    status.HTTP_400_BAD_REQUEST: "Некорректный запрос.",
    status.HTTP_401_UNAUTHORIZED: "Откройте Mini App через Telegram заново.",
    status.HTTP_403_FORBIDDEN: "Недостаточно прав для выполнения операции.",
    status.HTTP_404_NOT_FOUND: "Запрошенный ресурс не найден.",
    status.HTTP_409_CONFLICT: "Конфликт состояния ресурса.",
    413: "Файл превышает допустимый размер.",
    422: "Проверьте корректность заполненных полей.",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "Внутренняя ошибка сервера. Повторите попытку позже.",
    status.HTTP_503_SERVICE_UNAVAILABLE: "Сервис временно недоступен. Повторите попытку позже.",
    507: "На Яндекс.Диске недостаточно свободного места.",
}

HTTP_CODES = {
    status.HTTP_400_BAD_REQUEST: "invalid_request",
    status.HTTP_401_UNAUTHORIZED: "authentication_required",
    status.HTTP_403_FORBIDDEN: "admin_access_required",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_409_CONFLICT: "resource_conflict",
    413: "file_too_large",
    422: "validation_error",
    status.HTTP_503_SERVICE_UNAVAILABLE: "service_unavailable",
}

DETAIL_CODE_HINTS = {
    "Admin access required": "admin_access_required",
    "Only active users can upload files": "user_not_active",
    "File is too large": "file_too_large",
    "User not found": "user_not_found",
    "Request not found": "request_not_found",
    "Temp file not found": "request_not_found",
    "Invalid Telegram initData": "invalid_telegram_init_data",
    "Telegram initData expired": "telegram_init_data_expired",
    "Open the app via Telegram": "authentication_required",
}

DETAIL_MESSAGE_HINTS = {
    "Admin access required": "Недостаточно прав администратора.",
    "Only active users can upload files": "Загрузка доступна только активным пользователям.",
    "File is too large": "Файл превышает допустимый размер.",
    "User not found": "Пользователь не найден.",
    "Request not found": "Заявка не найдена.",
    "Temp file not found": "Временный файл не найден.",
    "Invalid Telegram initData": "Откройте Mini App через Telegram заново.",
    "Telegram initData expired": "Сессия Telegram истекла. Откройте Mini App заново.",
    "Open the app via Telegram": "Откройте Mini App через Telegram заново.",
}


class ApiError(FastAPIHTTPException):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.details = details
        super().__init__(status_code=status_code, detail=message, headers=headers)


def request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if not rid:
        rid = uuid.uuid4().hex
        request.state.request_id = rid
    return rid


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    rid = request_id(request)
    response_headers = dict(headers or {})
    response_headers["X-Request-ID"] = rid
    return JSONResponse(
        {"error": {"code": code, "message": message, "details": details}, "request_id": rid},
        status_code=status_code,
        headers=response_headers,
    )


def sanitized_exc_info(exc: Exception) -> tuple[type[BaseException], BaseException, Any]:
    safe_exc = RuntimeError(redact_text(exc)).with_traceback(exc.__traceback__)
    return (RuntimeError, safe_exc, exc.__traceback__)


def log_5xx(request: Request, exc: Exception) -> None:
    logger.error(
        "API request failed request_id=%s method=%s path=%s exc_type=%s",
        request_id(request),
        request.method,
        request.url.path,
        type(exc).__name__,
        exc_info=sanitized_exc_info(exc),
    )


def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    if exc.status_code >= 500:
        log_5xx(request, exc)
    return error_response(request, exc.status_code, exc.code, exc.message, exc.details, exc.headers)


def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    raw_detail = exc.detail if isinstance(exc.detail, str) else None
    code = DETAIL_CODE_HINTS.get(raw_detail or "") or HTTP_CODES.get(exc.status_code, "http_error")
    message = DETAIL_MESSAGE_HINTS.get(raw_detail or "") or DEFAULT_MESSAGES.get(
        exc.status_code, "Ошибка запроса."
    )
    if exc.status_code >= 500:
        log_5xx(request, exc)
    return error_response(request, exc.status_code, code, message, None, exc.headers)


def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"loc": list(err.get("loc", [])), "message": err.get("msg"), "type": err.get("type")}
        for err in exc.errors()
    ]
    return error_response(
        request,
        422,
        "validation_error",
        DEFAULT_MESSAGES[422],
        details,
    )


def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    log_5xx(request, exc)
    return error_response(
        request,
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "database_unavailable",
        "База данных временно недоступна. Повторите попытку позже.",
    )


def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log_5xx(request, exc)
    return error_response(
        request,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal_error",
        DEFAULT_MESSAGES[status.HTTP_500_INTERNAL_SERVER_ERROR],
    )
