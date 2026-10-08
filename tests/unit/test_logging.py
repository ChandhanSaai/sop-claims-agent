import json
import logging

from app.observability.logging import JsonFormatter, RedactionFilter, redact


def test_redact_masks_identifiers():
    s = (
        "DOB 1985-03-15, phone +16505212836 or 650-521-2836, "
        "email margaret@email.com, policy POL-9921, last4 4472"
    )
    out = redact(s)
    assert "1985-03-15" not in out
    assert "6505212836" not in out and "650-521-2836" not in out
    assert "margaret@email.com" not in out
    assert "POL-9921" not in out
    assert "[REDACTED]" in out


def test_redact_keeps_claim_ids_and_dates_in_prose():
    assert "CL-2048" in redact("claim CL-2048 was denied")


def test_json_formatter_applies_filter():
    logger = logging.getLogger("t")
    logger.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "email %s", ("margaret@email.com",), None)
    assert RedactionFilter().filter(record) is True
    line = JsonFormatter().format(record)
    data = json.loads(line)
    assert data["level"] == "INFO"
    assert "margaret@email.com" not in data["message"]
