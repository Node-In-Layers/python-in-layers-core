from __future__ import annotations

import logging
from datetime import datetime

from in_layers.core.globals.logging import (
    console_log_json,
    console_log_simple,
    console_log_full,
)
from in_layers.core.protocols import LogLevelNames


def _base_message():
    return {
        "id": "123",
        "datetime": datetime(2025, 1, 1, 0, 0, 0),
        "log_level": LogLevelNames.info,
        "message": "Test message",
        "logger": "test:domain:layer:function",
        "environment": "test",
    }


def test_console_log_simple(caplog):
    msg = _base_message()
    with caplog.at_level(logging.INFO):
        console_log_simple(msg)
    assert any(m.endswith(": function Test message") for m in caplog.messages)


def test_console_log_full_with_ids(caplog):
    msg = _base_message()
    msg["ids"] = [{"key1": "value1"}, {"key2": "value2"}]
    expected = "test info 123 [test:domain:layer:function] {key1:value1;key2:value2} Test message"
    with caplog.at_level(logging.INFO):
        console_log_full(msg)
    assert any(expected in m for m in caplog.messages)


def test_console_log_json(caplog):
    msg = _base_message()
    with caplog.at_level(logging.INFO):
        console_log_json(msg)
    combined = "\n".join(caplog.messages)
    assert (
        '"id": "123"' in combined
        and '"log_level": "info"' in combined
        and '"logger": "test:domain:layer:function"' in combined
    )
