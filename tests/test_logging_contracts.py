"""Logging must preserve fields, level filtering, and exception information."""

import json
import logging
from uuid import uuid4

import pytest

from chatterpal.utils import logger as log_module


@pytest.fixture
def configured_logger(tmp_path, monkeypatch):
    logger = log_module.ChatterPalLogger(f"chatterpal.contract.{uuid4().hex}")
    logger.logger.propagate = False
    monkeypatch.setattr(log_module, "_global_logger", logger)
    yield logger
    for handler in logger.logger.handlers[:]:
        handler.close()
        logger.logger.removeHandler(handler)


def test_json_logging_preserves_unicode_fields_levels_and_traceback(
    configured_logger, tmp_path
):
    path = tmp_path / "nested" / "events.jsonl"
    configured_logger.configure(
        level="DEBUG", log_file=str(path), console_output=False, json_format=True
    )
    configured_logger.debug("debug event", request_id="one")
    configured_logger.info("你好", session="session-one")
    configured_logger.warning("slow", elapsed_ms=12)
    configured_logger.error("provider unavailable")
    configured_logger.critical("unrecoverable", retry=False)
    try:
        raise ValueError("provider returned invalid audio")
    except ValueError:
        configured_logger.exception("synthesis failed", provider="offline")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["level"] for row in rows] == [
        "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "ERROR"
    ]
    assert rows[0]["request_id"] == "one"
    assert rows[1]["message"] == "你好"
    assert rows[1]["session"] == "session-one"
    assert rows[2]["elapsed_ms"] == 12
    assert rows[4]["retry"] is False
    assert "ValueError: provider returned invalid audio" in rows[5]["exception"]
    assert rows[5]["provider"] == "offline"
    assert all(row["logger"] == configured_logger.name for row in rows)
    assert all(row["timestamp"] for row in rows)


def test_extra_fields_obey_log_level_filter(configured_logger, tmp_path):
    path = tmp_path / "filtered.jsonl"
    configured_logger.configure(
        level="WARNING", log_file=str(path), console_output=False, json_format=True
    )
    configured_logger.debug("hidden debug", request_id="one")
    configured_logger.info("hidden info", request_id="two")
    configured_logger.warning("visible warning", request_id="three")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [(row["message"], row["request_id"]) for row in rows] == [
        ("visible warning", "three")
    ]


def test_colored_console_does_not_add_ansi_to_file_or_mutate_record(
    configured_logger, tmp_path, capsys
):
    path = tmp_path / "plain.log"
    configured_logger.configure(log_file=str(path), colored_output=True)
    configured_logger.info("A plain file event")
    assert "\033[32mINFO" in capsys.readouterr().out
    file_text = path.read_text()
    assert "INFO - A plain file event" in file_text
    assert "\033[" not in file_text
    record = logging.LogRecord("contract", logging.WARNING, __file__, 1, "warning", (), None)
    assert "\033[33mWARNING" in log_module.ColoredFormatter("%(levelname)s").format(record)
    assert record.levelname == "WARNING"


def test_log_file_rotation_and_repeat_configure_are_bounded(
    configured_logger, tmp_path
):
    path = tmp_path / "rotated.log"
    configured_logger.configure(
        log_file=str(path), console_output=False, max_file_size=180, backup_count=2
    )
    handlers = configured_logger.logger.handlers[:]
    configured_logger.configure(level="DEBUG", log_file=str(tmp_path / "ignored.log"))
    assert configured_logger.logger.handlers == handlers
    assert configured_logger.logger.level == logging.INFO
    for index in range(12):
        configured_logger.info(f"event-{index:02d} with a bounded rotation payload")
    assert "event-11" in path.read_text()
    assert (tmp_path / "rotated.log.1").exists()
    assert (tmp_path / "rotated.log.2").exists()
    assert not (tmp_path / "rotated.log.3").exists()
    assert not (tmp_path / "ignored.log").exists()


def test_logging_decorators_preserve_results_and_original_exception(
    configured_logger, monkeypatch, tmp_path
):
    path = tmp_path / "calls.jsonl"
    configured_logger.configure(
        level="DEBUG", log_file=str(path), console_output=False, json_format=True
    )
    monkeypatch.setattr(log_module, "get_logger", lambda *args: configured_logger)

    @log_module.log_function_call
    def multiply(value, factor=2):
        return value * factor

    @log_module.log_function_call
    def fail_call():
        raise LookupError("missing transcript")

    @log_module.log_performance
    def measured(value):
        return value + 1

    @log_module.log_performance
    def fail_measured():
        raise TimeoutError("local provider timeout")

    assert multiply(3, factor=4) == 12
    with pytest.raises(LookupError, match="missing transcript"):
        fail_call()
    assert measured(4) == 5
    with pytest.raises(TimeoutError, match="local provider timeout"):
        fail_measured()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert any(row.get("function", "").endswith("multiply") for row in rows)
    failed = next(row for row in rows if row.get("error") == "missing transcript")
    assert "LookupError: missing transcript" in failed["exception"]
    measured_rows = [row for row in rows if "execution_time" in row]
    assert len(measured_rows) == 2
    assert all(float(row["execution_time"][:-1]) >= 0 for row in measured_rows)
    assert measured_rows[1]["error"] == "local provider timeout"


def test_global_helpers_and_mixin_use_configured_logger(configured_logger, monkeypatch, tmp_path):
    monkeypatch.setattr(log_module, "get_logger", lambda *args: configured_logger)
    path = tmp_path / "global.jsonl"
    assert log_module.setup_logging({
        "level": "DEBUG", "log_file": str(path),
        "console_output": False, "json_format": True,
    }) is configured_logger
    component = log_module.LoggerMixin()
    assert component.logger is configured_logger
    assert component.logger is component.logger
    for name in ("debug", "info", "warning", "error", "critical"):
        getattr(log_module, name)(f"global {name}", source="helper")
    try:
        raise RuntimeError("global failure")
    except RuntimeError:
        log_module.exception("global exception")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["message"] for row in rows] == [
        "global debug", "global info", "global warning", "global error", "global critical",
        "global exception",
    ]
    assert all(row["source"] == "helper" for row in rows[:-1])
    assert "RuntimeError: global failure" in rows[-1]["exception"]
