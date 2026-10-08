import json
import logging
import re
from datetime import UTC, datetime

_PATTERNS = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),  # ISO dates (DOBs). Claim dates in prose use month names.
    re.compile(r"\+?\d[\d\-\s().]{8,}\d"),  # phone-like digit runs (10+ digits with separators)
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),  # emails
    re.compile(r"\bPOL-\d+\b"),  # policy numbers
]


def redact(text: str) -> str:
    for p in _PATTERNS:
        text = p.sub("[REDACTED]", text)
    return text


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.msg))
        if record.args:
            record.args = tuple(redact(str(a)) for a in record.args)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "session_id", "turn"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    root.addHandler(handler)
    root.setLevel(level.upper())
