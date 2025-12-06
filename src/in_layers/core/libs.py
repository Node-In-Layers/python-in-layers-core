from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .protocols import (
    CoreNamespace,
    CrossLayerProps,
    ErrorDetails,
    ErrorObject,
    LayerDescription,
    LogId,
    LogLevel,
    LogLevelNames,
)


def get_log_level_name(log_level: LogLevel) -> str:
    if log_level == LogLevel.TRACE:
        return "TRACE"
    if log_level == LogLevel.DEBUG:
        return "DEBUG"
    if log_level == LogLevel.INFO:
        return "INFO"
    if log_level == LogLevel.WARN:
        return "WARN"
    if log_level == LogLevel.ERROR:
        return "ERROR"
    if log_level == LogLevel.SILENT:
        return "SILENT"
    raise ValueError(f"Unhandled log level {log_level}")


def get_log_level_number(log_level: LogLevelNames) -> int:
    if log_level == LogLevelNames.trace:
        return LogLevel.TRACE.value
    if log_level == LogLevelNames.debug:
        return LogLevel.DEBUG.value
    if log_level == LogLevelNames.info:
        return LogLevel.INFO.value
    if log_level == LogLevelNames.warn:
        return LogLevel.WARN.value
    if log_level == LogLevelNames.error:
        return LogLevel.ERROR.value
    if log_level == LogLevelNames.silent:
        return LogLevel.SILENT.value
    raise ValueError(f"Unhandled log level {log_level}")


def _get_layer_key(layer: LayerDescription) -> str:
    if isinstance(layer, list):
        return "-".join(layer)
    return str(layer)


def get_layers_unavailable(
    all_layers: Sequence[LayerDescription],
) -> Callable[[str], list[str]]:
    layer_to_choices: dict[str, list[str]] = {}
    for idx, layer in enumerate(all_layers):
        anti_layers = list(all_layers[idx + 1 :])
        if isinstance(layer, list):
            for i, composite_layer in enumerate(layer):
                nested_anti = layer[i + 1 :]
                layer_to_choices[composite_layer] = [
                    choice for choice in _flatten_layers(anti_layers + nested_anti)
                ]
        else:
            layer_to_choices[_get_layer_key(layer)] = [
                choice for choice in _flatten_layers(anti_layers)
            ]

    def resolver(layer_name: str) -> list[str]:
        if layer_name not in layer_to_choices:
            raise ValueError(f"{layer_name} is not a valid layer choice")
        return layer_to_choices[layer_name]

    return resolver


def _flatten_layers(layers: Sequence[LayerDescription]) -> list[str]:
    result: list[str] = []
    for layer in layers:
        if isinstance(layer, list):
            result.extend(layer)
        else:
            result.append(layer)
    return result


def is_config(obj: Any) -> bool:
    if isinstance(obj, str):
        return False
    try:
        core = obj[CoreNamespace.root]  # type: ignore[index]
        _ = core["layer_order"]
        return True
    except Exception:
        return False


def validate_config(config: Mapping[str, Any]) -> None:
    def _require(path: list[str | CoreNamespace], type_: type | None = None) -> None:
        cur: Any = config
        for key in path:
            key_s = key.value if isinstance(key, CoreNamespace) else key
            if not isinstance(cur, Mapping) or key_s not in cur:
                raise ValueError(f"{'.'.join(map(str, path))} was not found in config")
            cur = cur[key_s]
        if type_ is not None and not isinstance(cur, type_):
            raise ValueError(
                f"{'.'.join(map(str, path))} must be of type {type_.__name__}"
            )

    _require(["environment"])
    _require(["system_name"])
    _require([CoreNamespace.root, "apps"])
    if not isinstance(config[CoreNamespace.root.value]["apps"], list):
        raise ValueError(f"{CoreNamespace.root.value}.apps must be an array")
    _require([CoreNamespace.root, "layer_order"])
    if not isinstance(config[CoreNamespace.root.value]["layer_order"], list):
        raise ValueError(f"{CoreNamespace.root.value}.layer_order must be an array")
    _require([CoreNamespace.root, "logging", "log_level"])
    _require([CoreNamespace.root, "logging", "log_format"])
    for app in config[CoreNamespace.root.value]["apps"]:
        if not isinstance(app, Mapping) or "name" not in app:
            raise ValueError("A configured app does not have a name.")


def combine_cross_layer_props(
    a: CrossLayerProps, b: CrossLayerProps
) -> CrossLayerProps:
    a_ids = list(a.get("logging", {}).get("ids", [])) if a.get("logging") else []
    b_ids = list(b.get("logging", {}).get("ids", [])) if b.get("logging") else []

    existing = {f"{k}:{v}": True for obj in a_ids for k, v in obj.items()}
    unique: list[LogId] = []
    for obj in b_ids:
        for k, v in obj.items():
            key = f"{k}:{v}"
            if key not in existing:
                unique.append({k: v})
    final_ids = a_ids + unique
    logging_other = dict(a.get("logging", {}))
    logging_other.pop("ids", None)
    result: CrossLayerProps = {"logging": {"ids": final_ids, **logging_other}}
    return result


def _convert_error_to_cause(error: Exception, code: str, message: str) -> ErrorObject:
    err: ErrorDetails = {"code": code, "message": message or str(error)}
    if getattr(error, "message", None):
        err["details"] = str(error)
    cause = getattr(error, "__cause__", None)
    if isinstance(cause, Exception):
        cause_obj = _convert_error_to_cause(cause, "NestedError", str(cause))
        err["cause"] = cause_obj["error"]
    return {"error": err}


def create_error_object(
    code: str, message: str, error: Any | None = None
) -> ErrorObject:
    base: ErrorObject = {"error": {"code": code, "message": message}}
    if error is None:
        return base
    if isinstance(error, Exception):
        details: ErrorObject = {
            "error": {
                "details": str(error),
                "message": message,
                "code": code,
            }
        }
        cause = getattr(error, "__cause__", None)
        if isinstance(cause, Exception):
            cause_obj = _convert_error_to_cause(cause, "CauseError", str(cause))
            details["error"]["cause"] = cause_obj["error"]
        return _merge(base, details)
    if isinstance(error, str):
        return _merge(base, {"error": {"details": error}})
    if isinstance(error, Mapping):
        try:
            json.dumps(error)
            return _merge(base, {"error": {"data": dict(error)}})
        except Exception:
            return _merge(base, {"error": {"details": str(error)}})
    return _merge(base, {"error": {"details": str(error)}})


def is_error_object(value: Any) -> bool:
    return isinstance(value, Mapping) and "error" in value


def _merge(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], Mapping) and isinstance(v, Mapping):
            out[k] = _merge(out[k], v)  # type: ignore[assignment]
        else:
            out[k] = v  # type: ignore[assignment]
    return out


def get_namespace(package_name: str, app: str | None = None) -> str:
    if app:
        return f"{package_name}/{app}"
    return package_name


def do_nothing_fetcher(model: Any, primary_key: Any) -> Any:  # noqa: ARG001
    return primary_key
