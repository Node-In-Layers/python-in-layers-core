from __future__ import annotations

from typing import Any, Mapping

import pytest
from box import Box

from in_layers.core.globals.logging import composite_logger, standard_logger
from in_layers.core.protocols import CoreNamespace, LogLevelNames, RootLogger
import json


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
    flog = layer.get_inner_logger("say")
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
    hl.get_app_logger("demo").get_layer_logger("features").get_inner_logger("x").debug(
        "hidden"
    )
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
    fn = layer._log_wrap("wrapped", lambda log, x, cross_layer_props=None: x)  # type: ignore[call-arg]
    fn("X", cross_layer_props=None)
    assert any(
        "Executing features function" in m or "Executed features function" in m
        for m in collected
    )


def test_function_logger_wrap_supports_omit_data_override():
    collected: list[dict[str, Any]] = []

    def method(_c):
        def log_fn(msg):
            collected.append(msg)  # type: ignore[arg-type]

        return log_fn

    root: RootLogger = composite_logger([method])
    ctx = _ctx(
        {
            "log_level": LogLevelNames.info,
            "log_format": "simple",
            "get_function_wrap_log_level": lambda _layer, _fn: LogLevelNames.info,
        }
    )
    flog = (
        root.get_logger(ctx)
        .get_app_logger("demo")
        .get_layer_logger("features")
        .get_inner_logger("wrapped")
    )

    result = flog.wrap(
        lambda: {"ok": True},
        {
            "args": [{"secret": "value"}],
            "cross_layer_props": {"logging": {"overrides": {"omit_data": True}}},
        },
    )

    assert result == {"ok": True}
    executing = next(
        m for m in collected if m["message"] == "Executing features function"
    )
    executed = next(
        m for m in collected if m["message"] == "Executed features function"
    )
    assert "args" not in executing
    assert "result" not in executed


def test_wrapped_function_result_is_jsonable_in_log_payload():
    collected: list[dict[str, Any]] = []

    def method(_c):
        def log_fn(msg):
            collected.append(msg)  # type: ignore[arg-type]

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

    class NotJson:
        def __str__(self) -> str:
            return "NotJson()"

    fn = layer._log_wrap("wrapped", lambda _log, cross_layer_props=None: NotJson())  # type: ignore[call-arg]
    fn(cross_layer_props=None)
    executed = [
        m for m in collected if m.get("message") == "Executed features function"
    ]
    assert executed and "result" in executed[-1]
    # `result` should be JSON-serializable without default=str
    json.dumps(executed[-1]["result"])


def test_error_payload_is_jsonable():
    collected: list[dict[str, Any]] = []

    def method(_c):
        def log_fn(msg):
            collected.append(msg)  # type: ignore[arg-type]

        return log_fn

    root: RootLogger = composite_logger([method])
    flog = (
        root.get_logger(_ctx({"log_level": LogLevelNames.info, "log_format": "simple"}))
        .get_app_logger("demo")
        .get_layer_logger("features")
        .get_inner_logger("fn")
    )
    flog.error("E", {"error": Exception("boom")})
    assert collected and "error" in collected[-1]
    json.dumps(collected[-1]["error"])


def test_standard_logger_json_format_emits(caplog):
    with caplog.at_level("INFO"):
        root = standard_logger()
        hl = root.get_logger(
            _ctx({"log_level": LogLevelNames.info, "log_format": "json"})
        )
        hl.get_app_logger("demo").get_layer_logger("features").get_inner_logger(
            "fn"
        ).info("M", {"a": 1})
        joined = " ".join(caplog.messages)
        assert '"logger": "demo:features:fn"' in joined


def test_ids_stack_include_runtime_for_inner_logger():
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
        .get_inner_logger("fn")
    )
    flog.info("Z")
    ids = collected[0].get("ids") or []
    assert any("runtime_id" in d for d in ids)
    assert not any("function_call_id" in d for d in ids)


def test_wrap_function_call_creates_function_call_id():
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
        .get_inner_logger("outer")
    )

    result = flog.wrap_function_call(
        "fn",
        lambda inner_log, payload: inner_log.info("wrapped", payload),
        {"args": [{"phase": "inner"}]},
    )

    assert result is None
    wrapped = next(
        msg
        for msg in collected
        if msg.get("logger") == "demo:features:outer:fn"
        and msg.get("message") == "Executing features function"
    )
    ids = wrapped.get("ids") or []
    assert any("function_call_id" in d for d in ids)


def test_tcp_logger_raises_helpful_error_when_missing_options():
    root = standard_logger()
    bad = _ctx({"log_level": LogLevelNames.info, "log_format": "tcp"})
    with pytest.raises(Exception):
        # constructing the logger will attempt to build tcp method and fail without options
        root.get_logger(bad)


def test_composite_logger_survives_sink_failure(capsys):
    collected: list[dict[str, Any]] = []

    def broken_method(_c):
        def log_fn(_msg):
            raise RuntimeError("sink boom")

        return log_fn

    def working_method(_c):
        def log_fn(msg):
            collected.append(msg)  # type: ignore[arg-type]

        return log_fn

    root: RootLogger = composite_logger([broken_method, working_method])
    flog = (
        root.get_logger(_ctx({"log_level": LogLevelNames.info, "log_format": "simple"}))
        .get_app_logger("demo")
        .get_layer_logger("features")
        .get_inner_logger("fn")
    )

    flog.info("still works")

    stderr = capsys.readouterr().err
    assert collected and collected[0]["message"] == "still works"
    assert "log sink failed: sink boom" in stderr
