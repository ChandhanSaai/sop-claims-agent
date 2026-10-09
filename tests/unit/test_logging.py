import json
import logging

from app.observability.logging import JsonFormatter, RedactionFilter, configure_logging, redact


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
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "email %s", ("margaret@email.com",), None)
    assert RedactionFilter().filter(record) is True
    line = JsonFormatter().format(record)
    data = json.loads(line)
    assert data["level"] == "INFO"
    assert "margaret@email.com" not in data["message"]


def test_configure_logging_emits_redacted_json_for_every_record(capsys, caplog):
    root = logging.getLogger()
    level = root.level
    configure_logging("INFO")
    handler = root.handlers[-1]  # configure_logging appends its handler; it writes to capsys' stderr
    log = logging.getLogger("probe")
    log.info("turn %d of %s", 3, "margaret@email.com")
    log.info("caller %(name)s", {"name": "margaret@email.com"})
    try:
        raise ValueError("lookup failed for margaret@email.com")
    except ValueError:
        log.exception("reader failed")
    root.removeHandler(handler)  # its stream closes with capsys when this test ends
    root.setLevel(level)
    err = capsys.readouterr().err
    assert "Logging error" not in err
    lines = [json.loads(line) for line in err.splitlines()]
    assert len(lines) == 3
    assert lines[0]["message"] == "turn 3 of [REDACTED]"
    assert lines[1]["message"] == "caller [REDACTED]"
    assert "[REDACTED]" in lines[2]["exc"] and "margaret@email.com" not in lines[2]["exc"]
    assert len(caplog.records) == 3  # pytest's capture handler survived configure_logging
