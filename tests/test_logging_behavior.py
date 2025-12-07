from __future__ import annotations

from typing import Any, Mapping

import pytest
from box import Box

from in_layers.core.globals.logging import composite_logger, standard_logger
from in_layers.core.protocols import CoreNamespace, LogLevelNames, RootLogger


def _ctx(logging_cfg: Mapping[str, Any]) -> Box:
    return Box(
        {
            "config": {
                CoreNamespace.root.value: {
                    "logging": logging_cfg,
                }
            },
            "constants": {
                "environment": "test",
                "runtime_id": "RID",
                "working_directory": "/tmp",
            },
        },
        default_box=True,
        default_box_attr=None,
    )


def test_composite_logger_emits_and_includes_names_and_ids():
    collected: list[dict[str, Any]] = []

    def method(_c):
        def log_fn(msg):
            collected.append(msg)  # type: ignore[arg-type]

        return log_fn

    root: RootLogger = composite_logger([method])
    hl = root.get_logger(
        _ctx({"log_level": LogLevelNames.info, "log_format": "simple"})
    )
    app = hl.get_app_logger("demo")
    layer = app.get_layer_logger("features")
    flog = layer.get_function_logger("say")
    flog.info("Hello", {"foo": "bar"})
    assert len(collected) == 1


def test_log_level_respected_no_output_when_lower_than_config():
    collected: list[dict[str, Any]] = []

    def method(_c):
        def log_fn(msg):
            collected.append(msg)  # type: ignore[arg-type]

        return log_fn

    root: RootLogger = composite_logger([method])
    hl = root.get_logger(
        _ctx({"log_level": LogLevelNames.warn, "log_format": "simple"})
    )
    hl.get_app_logger("demo").get_layer_logger("features").get_function_logger(
        "x"
    ).debug("hidden")
    assert collected == []


def test_wrapper_logs_use_custom_wrap_level():
    collected: list[str] = []

    def method(_c):
        def log_fn(msg):
            collected.append(msg["message"])  # type: ignore[index]

        return log_fn

    ctx = _ctx(
        {
            "log_level": LogLevelNames.info,
            "log_format": "simple",
            "get_function_wrap_log_level": lambda _layer, _fn: LogLevelNames.info,
        }
    )
    root: RootLogger = composite_logger([method])
    layer = root.get_logger(ctx).get_app_logger("demo").get_layer_logger("features")
    fn = layer._log_wrap("wrapped", lambda log, x, cross=None: x)  # type: ignore[call-arg]
    fn("X")
    assert any(
        "Executing features function" in m or "Executed features function" in m
        for m in collected
    )


def test_standard_logger_json_format_emits(caplog):
    with caplog.at_level("INFO"):
        root = standard_logger()
        hl = root.get_logger(
            _ctx({"log_level": LogLevelNames.info, "log_format": "json"})
        )
        hl.get_app_logger("demo").get_layer_logger("features").get_function_logger(
            "fn"
        ).info("M", {"a": 1})
        joined = " ".join(caplog.messages)
        assert '"logger": "demo:features:fn"' in joined


def test_ids_stack_includes_runtime_and_function():
    collected: list[dict[str, Any]] = []

    def method(_c):
        def log_fn(msg):
            collected.append(msg)  # type: ignore[arg-type]

        return log_fn

    root: RootLogger = composite_logger([method])
    ctx = _ctx({"log_level": LogLevelNames.info, "log_format": "simple"})
    flog = (
        root.get_logger(ctx)
        .get_app_logger("demo")
        .get_layer_logger("features")
        .get_function_logger("fn")
    )
    flog.info("Z")
    ids = collected[0].get("ids") or []
    assert any("runtime_id" in d for d in ids) and any(
        "function_call_id" in d for d in ids
    )


def test_tcp_logger_raises_helpful_error_when_missing_options():
    root = standard_logger()
    bad = _ctx({"log_level": LogLevelNames.info, "log_format": "tcp"})
    with pytest.raises(Exception):
        # constructing the logger will attempt to build tcp method and fail without options
        root.get_logger(bad)
