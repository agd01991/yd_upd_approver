import logging
import re

TOKEN_PATTERNS = [
    re.compile(r"(Authorization[\'\"]?\s*:?\s*[\'\"]?(?:Bearer|OAuth)\s+)[^\'\"\s,}]+", re.I),
    re.compile(r"(X-Telegram-Init-Data[\'\"]?\s*:?\s*[\'\"]?)[^\'\"\s,}]+", re.I),
    re.compile(r"(initData[\'\"]?\s*:?\s*[\'\"]?)[^\'\"\s,}]+", re.I),
    re.compile(r"(postgresql(?:\+\w+)?://[^:\s/@]+:)[^@\s]+(@[^\s]+)", re.I),
    re.compile(
        r"((?:access_token|oauth_token|bot_token|password|passwd|pwd|cookie)[\'\"]?\s*[:=]\s*[\'\"]?)[^\'\"\s,}]+",
        re.I,
    ),
    re.compile(r"\d{8,}:[\w-]+"),
]


def redact_text(value: object) -> str:
    message = str(value)
    for pattern in TOKEN_PATTERNS:
        message = pattern.sub(
            r"\1***\2" if pattern.groups >= 2 else (r"\1***" if pattern.groups else "***"), message
        )
    return message


class SecretRedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = redact_text(record.getMessage())
        record.msg = message
        record.args = ()
        return True


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    redacting_filter = SecretRedactingFilter()
    root = logging.getLogger()
    root.addFilter(redacting_filter)
    for handler in root.handlers:
        handler.addFilter(redacting_filter)
