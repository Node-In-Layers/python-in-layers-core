from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from box import Box

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
    if type(obj) is str:
        return False
    if isinstance(obj, dict):
        try:
            validate_config(obj)
            return True
        except ValueError:
            return False
    return False


def validate_config(config: Mapping[str, Any]) -> None:
    def _require(path: list[str | CoreNamespace], type_: type | None = None) -> None:
        cur: Any = config
        for key in path:
            key_s = key.value if isinstance(key, CoreNamespace) else key
            if key_s not in cur:
                raise ValueError(f"{'.'.join(map(str, path))} was not found in config")
            cur = cur[key_s]
        if type_ is not None and not isinstance(cur, type_):
            raise ValueError(
                f"{'.'.join(map(str, path))} must be of type {type_.__name__}"
            )

    _require(["environment"])
    _require(["system_name"])
    _require([CoreNamespace.root.value, "domains"])
    if not isinstance(config.in_layers_core.domains, list):
        raise ValueError(f"{CoreNamespace.root.value}.domains must be an array")
    _require([CoreNamespace.root.value, "layer_order"])
    if not isinstance(config.in_layers_core.layer_order, list):
        raise ValueError(f"{CoreNamespace.root}.layer_order must be an array")
    _require([CoreNamespace.root.value, "logging", "log_level"])
    _require([CoreNamespace.root.value, "logging", "log_format"])
    for domain in config.in_layers_core.domains:
        try:
            name = domain.name  # noqa: F841
        except AttributeError as e:
            raise ValueError("A configured domain does not have a name.") from e


def _normalize_cross_layer_props(props: CrossLayerProps) -> Box:
    """
    Accept either dict-like or object-shaped CrossLayerProps; return a Box.
    Box acts as a dict (.get, []) and supports kwargs-style attribute access.
    """
    if not props:
        return Box({"logging": {"ids": []}}, default_box=True)
    if isinstance(props, Mapping):
        logging_val = props.get("logging")
    else:
        logging_val = getattr(props, "logging", None)
    if not logging_val:
        return Box({"logging": {"ids": []}}, default_box=True)
    if isinstance(logging_val, Mapping):
        ids = list(logging_val.get("ids", []))
        other = {k: v for k, v in logging_val.items() if k != "ids"}
    else:
        ids = list(getattr(logging_val, "ids", []))
        other = {}
    return Box({"logging": {"ids": ids, **other}}, default_box=True)


def normalize_cross_layer_props(props: CrossLayerProps | None) -> Box | None:
    """
    Convert CrossLayerProps (dict, Box, or Pydantic/object instance) to Box.
    Returns None if props is None. Use so framework and user functions
    always receive Box when cross_layer_props is present.
    """
    if props is None:
        return None
    return _normalize_cross_layer_props(props)


def combine_cross_layer_props(
    a: CrossLayerProps, b: CrossLayerProps
) -> CrossLayerProps:
    a_norm = _normalize_cross_layer_props(a)
    b_norm = _normalize_cross_layer_props(b)
    a_ids = list(a_norm.get("logging", {}).get("ids", []))
    b_ids = list(b_norm.get("logging", {}).get("ids", []))

    existing = {f"{k}:{v}": True for obj in a_ids for k, v in obj.items()}
    unique: list[LogId] = []
    for obj in b_ids:
        for k, v in obj.items():
            key = f"{k}:{v}"
            if key not in existing:
                unique.append({k: v})
    final_ids = a_ids + unique
    logging_other = dict(a_norm.get("logging", {}))
    logging_other.pop("ids", None)
    result: CrossLayerProps = Box(
        {"logging": {"ids": final_ids, **logging_other}}, default_box=True
    )
    return result


def _convert_error_to_cause(error: Exception, code: str, message: str) -> ErrorDetails:
    err: ErrorDetails = {"code": code, "message": message or str(error)}
    if getattr(error, "message", None):
        err["details"] = str(error)
    cause = getattr(error, "__cause__", None)
    if isinstance(cause, Exception):
        cause_obj = _convert_error_to_cause(cause, "NestedError", str(cause))
        err["cause"] = cause_obj
    return err


def create_error_object(
    code: str, message: str, error: Any | None = None, details: str | None = None
) -> ErrorObject:
    base = ErrorObject(error=ErrorDetails(code=code, message=message))
    if error is None:
        return base
    if isinstance(error, Exception):
        cause = getattr(error, "__cause__", None)
        if isinstance(cause, Exception):
            cause = _convert_error_to_cause(cause, "CauseError", str(cause))
        return ErrorObject(
            error=ErrorDetails(
                code=code, message=message, details=details or str(error), cause=cause
            )
        )
    if isinstance(error, str):
        return ErrorObject(
            error=ErrorDetails(code=code, message=message, details=details or error)
        )
    if isinstance(error, Mapping):
        try:
            json.dumps(error)
            return ErrorObject(
                error=ErrorDetails(
                    code=code, message=message, details=details, data=dict(error)
                )
            )
        except Exception:
            return ErrorObject(
                error=ErrorDetails(
                    code=code, message=message, details=details or str(error)
                )
            )
    return ErrorObject(
        error=ErrorDetails(code=code, message=message, details=details or str(error))
    )


def is_error_object(value: Any) -> bool:
    if isinstance(value, ErrorObject):
        return True
    return isinstance(value, Mapping) and (
        "error" in value and value["error"] is not None
    )


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


def is_cross_layer_props(value: Any) -> bool:
    """
    True only for dict-shaped cross layer props: Mapping with 'logging' and 'ids' list.
    Does not return True for object/instance-shaped (e.g. Pydantic). Use
    is_object_shaped_cross_layer_props for that; call sites must handle that case
    separately and normalize to Box.
    """
    if value is None:
        return False
    if not isinstance(value, Mapping):
        return False
    logging_val = value.get("logging")
    return isinstance(logging_val, Mapping) and isinstance(logging_val.get("ids"), list)


def is_object_shaped_cross_layer_props(value: Any) -> bool:
    """
    True when value is cross-layer-props-shaped but as an object (e.g. Pydantic
    instance from FastMCP), not a dict. Such values must be normalized to Box
    before use; call sites must check this and normalize.
    """
    if value is None:
        return False
    if isinstance(value, Mapping):
        return False
    try:
        logging_val = getattr(value, "logging", None)
        if logging_val is None:
            return False
        ids = (
            logging_val.get("ids")
            if isinstance(logging_val, Mapping)
            else getattr(logging_val, "ids", None)
        )
        return isinstance(ids, list)
    except Exception:
        return False


def do_nothing_fetcher(model: Any, primary_key: Any) -> Any:  # noqa: ARG001
    return primary_key
