from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from ..globals.libs import cap_for_logging
from ..protocols import CommonContext, CoreNamespace, LogId, LogLevelNames, LogMessage

OTEL_ID_ATTRIBUTE_PREFIX = "id_"
SPAN_KIND_INTERNAL = 0
SPAN_KIND_SERVER = 1
SPAN_KIND_CLIENT = 2

_FRAMEWORK_OTEL_KEYS = {
    "id",
    "logger",
    "environment",
    "log_level",
    "message",
    "datetime",
    "domain",
    "layer",
    "function",
    "model",
    "error",
    "args",
    "result",
}


def log_level_to_otel_severity(log_level: LogLevelNames) -> int:
    mapping = {
        LogLevelNames.trace: 1,
        LogLevelNames.debug: 5,
        LogLevelNames.info: 9,
        LogLevelNames.warn: 13,
        LogLevelNames.error: 17,
        LogLevelNames.silent: 0,
    }
    return mapping.get(log_level, 9)


def ids_to_attributes(ids: list[LogId] | None) -> dict[str, str] | None:
    if not ids:
        return None
    grouped: dict[str, list[str]] = {}
    for obj in ids:
        if not isinstance(obj, Mapping):
            continue
        for key, value in obj.items():
            grouped.setdefault(str(key), []).append(str(value))
    if not grouped:
        return None
    out: dict[str, str] = {}
    for key, values in grouped.items():
        for index, value in enumerate(values):
            final_key = key if index == 0 else f"{key}_{index + 1}"
            out[final_key] = value
    return out or None


def ids_to_otel_attributes(ids: list[LogId] | None) -> dict[str, str] | None:
    flattened = ids_to_attributes(ids)
    if not flattened:
        return None
    return {
        f"{OTEL_ID_ATTRIBUTE_PREFIX}{key}": value for key, value in flattened.items()
    }


def layer_span_name(layer_name: str, domain: str, function_name: str) -> str:
    return f"{layer_name}:{domain}:{function_name}"


def layer_metric_attrs(
    layer_name: str, domain: str, function_name: str
) -> dict[str, str]:
    return {
        "layer": layer_name,
        "domain": domain,
        "function": function_name,
    }


def span_kind_for_layer(layer_name: str) -> int:
    if layer_name == "services":
        return SPAN_KIND_CLIENT
    if layer_name in {"entries", "features"}:
        return SPAN_KIND_SERVER
    return SPAN_KIND_INTERNAL


def span_attributes_from_ids(
    ids: list[LogId] | None,
    layer_name: str,
    domain: str,
    function_name: str,
) -> dict[str, Any]:
    return {
        **(ids_to_otel_attributes(ids) or {}),
        "domain": domain,
        "layer": layer_name,
        "function": function_name,
    }


def log_message_to_otel_attributes(
    log_message: LogMessage | Mapping[str, Any],
    context: CommonContext,
) -> dict[str, Any]:
    message = dict(log_message)
    max_log_chars = context.config.in_layers_core.logging.get(
        "max_log_size_in_characters", 50000
    )
    envelope = {
        "id": message.get("id"),
        "logger": message.get("logger"),
        "environment": message.get("environment"),
        "log_level": message.get("log_level"),
        "message": message.get("message"),
        "datetime": _to_iso(message.get("datetime")),
        **(ids_to_otel_attributes(message.get("ids")) or {}),
    }
    layer_attrs = {
        key: message[key]
        for key in ("domain", "layer", "function", "model")
        if key in message
    }
    capped_known = {
        key: cap_for_logging(message.get(key), max_log_chars)
        for key in ("args", "result", "error")
        if key in message
    }
    custom_attrs = {
        key: cap_for_logging(value, max_log_chars)
        for key, value in message.items()
        if key not in _FRAMEWORK_OTEL_KEYS
        and not str(key).startswith(OTEL_ID_ATTRIBUTE_PREFIX)
    }
    return {
        **envelope,
        **layer_attrs,
        **capped_known,
        **custom_attrs,
    }


def create_otel_log_method():
    def _method(context: CommonContext):
        services = _get_mapping_value(context, "services") or {}
        otel = _get_mapping_value(services, CoreNamespace.otel.value)
        emit = _get_mapping_value(_get_mapping_value(otel, "logs") or {}, "emit")
        if not callable(emit):
            return lambda _msg: None

        def _log(log_message: LogMessage | Mapping[str, Any]) -> None:
            emit(
                {
                    "body": dict(log_message).get("message", ""),
                    "severity_number": log_level_to_otel_severity(
                        dict(log_message).get("log_level", LogLevelNames.info)
                    ),
                    "severity_text": dict(log_message).get("log_level"),
                    "attributes": log_message_to_otel_attributes(log_message, context),
                }
            )

        return _log

    return _method


def create_noop_span():
    class _NoopSpan:
        def end(self) -> None:
            return None

        def set_attribute(self, key: str, value: Any) -> None:  # noqa: ARG002
            return None

        def set_status(self, status: Mapping[str, Any]) -> None:  # noqa: ARG002
            return None

        def add_event(
            self, _name: str, _attributes: Mapping[str, Any] | None = None
        ) -> None:
            return None

        def span_context(self):
            return None

    return _NoopSpan()


def create_noop_histogram():
    class _NoopHistogram:
        def record(
            self, _value: int | float, _attributes: Mapping[str, Any] | None = None
        ) -> None:
            return None

    return _NoopHistogram()


def create_noop_counter():
    class _NoopCounter:
        def add(
            self, _value: int | float = 1, _attributes: Mapping[str, Any] | None = None
        ) -> None:
            return None

    return _NoopCounter()


def _to_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _get_mapping_value(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)
