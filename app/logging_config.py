import logging
import re

TOKEN_PATTERNS = [
    re.compile(r"(Authorization[\'\"]?\s*:?\s*[\'\"]?(?:Bearer|OAuth)\s+)[^\'\"\s,}]+", re.I),
    re.compile(r"\d{8,}:[\w-]+"),
]


class SecretRedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for pattern in TOKEN_PATTERNS:
            message = pattern.sub(r"\1***" if pattern.groups else "***", message)
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
