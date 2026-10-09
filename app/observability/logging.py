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
        try:
            record.msg = redact(record.getMessage())
            record.args = None
        except Exception:  # malformed format call: redact the parts; the handler still reports the error
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
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload)


_handler: logging.Handler | None = None  # the root handler configure_logging installed last


def configure_logging(level: str = "INFO") -> None:
    global _handler
    root = logging.getLogger()
    if _handler is not None:
        root.removeHandler(_handler)  # only ours: pytest's caplog and other handlers stay
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    root.addHandler(handler)
    _handler = handler
    root.setLevel(level.upper())
