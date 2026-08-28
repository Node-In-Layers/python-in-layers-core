from __future__ import annotations

import functools
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from box import Box
from pydantic import TypeAdapter

from .protocols import (
    AnnotatedFunctionProps,
    CombineCrossLayerPropsOptions,
    CoreNamespace,
    CrossLayerProps,
    ErrorDetails,
    ErrorObject,
    LayerDescription,
    Logger,
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
    plain = _to_plain_data(props)
    logging_val = plain.get("logging", {})
    ids = list(logging_val.get("ids", [])) if isinstance(logging_val, Mapping) else []
    if not isinstance(logging_val, Mapping):
        logging_val = {}
    logging_other = {k: v for k, v in logging_val.items() if k != "ids"}
    final = {
        **{k: v for k, v in plain.items() if k != "logging"},
        "logging": {"ids": ids, **logging_other},
    }
    return Box(final, default_box=True)


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
    a: CrossLayerProps,
    b: CrossLayerProps,
    options: CombineCrossLayerPropsOptions | Mapping[str, Any] | None = None,
) -> CrossLayerProps:
    if not a and not b:
        return Box({"logging": {"ids": []}}, default_box=True)
    a_box = normalize_cross_layer_props(a) if a else Box({}, default_box=True)
    b_box = normalize_cross_layer_props(b) if b else Box({}, default_box=True)
    a_ids = list(a_box.get("logging", {}).get("ids", []))
    b_ids = list(b_box.get("logging", {}).get("ids", []))
    existing = {f"{k}:{v}": True for obj in a_ids for k, v in obj.items()}
    unique: list[LogId] = []
    for obj in b_ids:
        for k, v in obj.items():
            key = f"{k}:{v}"
            if key not in existing:
                unique.append({k: v})
    final_ids = a_ids + unique
    logging_a = dict(a_box.get("logging", {}))
    logging_b = dict(b_box.get("logging", {}))
    options_box = _to_plain_data(options or {})
    forward_baggage = bool(options_box.get("forward_baggage"))
    otel_a = logging_a.get("otel")
    otel_b = logging_b.get("otel")
    resolved_otel = otel_b if forward_baggage and otel_b is not None else otel_a
    logging_out = _merge(
        {k: v for k, v in logging_a.items() if k not in {"ids", "otel", "overrides"}},
        {k: v for k, v in logging_b.items() if k not in {"ids", "otel", "overrides"}},
    )
    if resolved_otel is not None:
        logging_out["otel"] = resolved_otel
    result = _merge(
        {k: v for k, v in a_box.items() if k != "logging"},
        {k: v for k, v in b_box.items() if k != "logging"},
    )
    result["logging"] = {"ids": final_ids, **logging_out}
    return Box(result, default_box=True)


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
        if not cause:
            cause = _convert_error_to_cause(error, "CauseError", str(error))
        return ErrorObject(
            error=ErrorDetails(code=code, message=message, details=details, cause=cause)
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
    if not isinstance(logging_val, Mapping):
        return False
    ids = logging_val.get("ids")
    if isinstance(ids, list):
        return True
    return any(key in logging_val for key in ("overrides", "otel"))


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
        if isinstance(ids, list):
            return True
        overrides = (
            logging_val.get("overrides")
            if isinstance(logging_val, Mapping)
            else getattr(logging_val, "overrides", None)
        )
        otel = (
            logging_val.get("otel")
            if isinstance(logging_val, Mapping)
            else getattr(logging_val, "otel", None)
        )
        return overrides is not None or otel is not None
    except Exception:
        return False


def do_nothing_fetcher(model: Any, primary_key: Any) -> Any:  # noqa: ARG001
    return primary_key


def get_otel_forward_baggage_from_config(
    config: Mapping[str, Any] | Any,
) -> Box | None:
    logging_cfg = _read_path(config, "in_layers_core", "logging")
    otel_cfg = _read_mapping(logging_cfg).get("otel", {})
    forward = _read_mapping(otel_cfg).get("forward_baggage")
    if forward is None:
        forward = _read_mapping(otel_cfg).get("forwardBaggage")
    if forward is True:
        return Box({"forward_baggage": True}, default_box=True)
    return None


def create_cross_layer_props(
    logger: Logger,
    cross_layer_props: CrossLayerProps | Mapping[str, Any] | None = None,
    options: CombineCrossLayerPropsOptions | Mapping[str, Any] | None = None,
) -> CrossLayerProps:
    base = (
        _strip_logging_overrides_from_cross_layer_props(cross_layer_props)
        if cross_layer_props
        else Box({}, default_box=True)
    )
    return combine_cross_layer_props(
        base,
        {"logging": {"ids": logger.get_ids()}},
        options,
    )


def cross_layer_props_with_logging_overrides(
    overrides: Mapping[str, Any],
    cross_layer_props: CrossLayerProps | Mapping[str, Any] | None = None,
) -> CrossLayerProps:
    base = normalize_cross_layer_props(cross_layer_props) or Box({}, default_box=True)
    logging_data = dict(base.get("logging", {}))
    prior = dict(_read_mapping(logging_data.get("overrides")))
    merged = _merge(prior, _normalize_overrides(overrides))
    result = _merge(
        {k: v for k, v in base.items() if k != "logging"},
        {"logging": {**logging_data, "overrides": merged}},
    )
    return Box(result, default_box=True)


def get_otel_baggage_from_cross_layer_props(
    cross_layer_props: CrossLayerProps | Mapping[str, Any] | None = None,
) -> Mapping[str, str] | None:
    if not cross_layer_props:
        return None
    normalized = normalize_cross_layer_props(cross_layer_props)
    baggage = normalized.get("logging", {}).get("otel", {}).get("baggage")
    if not isinstance(baggage, Mapping) or not baggage:
        return None
    return {str(k): str(v) for k, v in baggage.items()}


def cross_layer_props_with_otel_baggage(
    baggage: Mapping[str, str] | None,
    cross_layer_props: CrossLayerProps | Mapping[str, Any] | None = None,
) -> CrossLayerProps:
    base = normalize_cross_layer_props(cross_layer_props) or Box({}, default_box=True)
    logging_data = dict(base.get("logging", {}))
    if not baggage:
        logging_data.pop("otel", None)
    else:
        logging_data["otel"] = {"baggage": {str(k): str(v) for k, v in baggage.items()}}
    result = _merge(
        {k: v for k, v in base.items() if k != "logging"},
        {"logging": logging_data},
    )
    return Box(result, default_box=True)


def error_object_schema() -> dict[str, Any]:
    return TypeAdapter(ErrorObject).json_schema()


def annotation_function_props(
    args: AnnotatedFunctionProps,
) -> AnnotatedFunctionProps:
    return args


def annotated_function(
    props: AnnotatedFunctionProps | Mapping[str, Any],
    implementation: Callable[..., Any],
) -> Callable[..., Any]:
    props_map = _to_plain_data(props)
    args_adapter = TypeAdapter(props_map["args_schema"])
    returns_schema = props_map.get("returns_schema")
    returns_adapter = (
        TypeAdapter(returns_schema) if returns_schema is not None else None
    )

    @functools.wraps(implementation)
    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        if args:
            validated_args = args_adapter.validate_python(args[0])
            call_args = [validated_args, *args[1:]]
        else:
            call_args = list(args)
        result = implementation(*call_args, **kwargs)
        if returns_adapter is not None and not is_error_object(result):
            return returns_adapter.validate_python(result)
        return result

    _wrapped.function_name = props_map["function_name"]
    _wrapped.domain = props_map["domain"]
    _wrapped.description = props_map.get("description")
    _wrapped.args_schema = args_adapter
    _wrapped.returns_schema = returns_adapter
    _wrapped.schema = {
        "args": args_adapter.json_schema(),
        "returns": returns_adapter.json_schema() if returns_adapter else None,
        "error": error_object_schema(),
    }
    return _wrapped


def _to_plain_data(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return {str(k): _plain_leaf(v) for k, v in value.items()}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump) and not isinstance(value, type):
        return _to_plain_data(model_dump())
    vars_value = getattr(value, "__dict__", None)
    if isinstance(vars_value, dict):
        return {
            str(k): _plain_leaf(v)
            for k, v in vars_value.items()
            if not str(k).startswith("_")
        }
    return {}


def _plain_leaf(value: Any) -> Any:
    if isinstance(value, type):
        return value
    if isinstance(value, Mapping):
        return {str(k): _plain_leaf(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_leaf(v) for v in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump) and not isinstance(value, type):
        return _plain_leaf(model_dump())
    vars_value = getattr(value, "__dict__", None)
    if isinstance(vars_value, dict):
        return {
            str(k): _plain_leaf(v)
            for k, v in vars_value.items()
            if not str(k).startswith("_")
        }
    return value


def _read_path(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if isinstance(current, Mapping):
            current = current.get(key)
        else:
            current = getattr(current, key, None)
        if current is None:
            return None
    return current


def _read_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(k): v for k, v in value.items()}
    if value is None:
        return {}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _read_mapping(model_dump())
    vars_value = getattr(value, "__dict__", None)
    if isinstance(vars_value, dict):
        return {str(k): v for k, v in vars_value.items() if not str(k).startswith("_")}
    return {}


def _normalize_overrides(overrides: Mapping[str, Any]) -> dict[str, Any]:
    plain = _read_mapping(overrides)
    if "omitData" in plain and "omit_data" not in plain:
        plain["omit_data"] = plain["omitData"]
    return plain


def _strip_logging_overrides_from_cross_layer_props(
    cross_layer_props: CrossLayerProps | Mapping[str, Any],
) -> CrossLayerProps:
    normalized = normalize_cross_layer_props(cross_layer_props) or Box(
        {}, default_box=True
    )
    logging_data = dict(normalized.get("logging", {}))
    logging_data.pop("overrides", None)
    result = _merge(
        {k: v for k, v in normalized.items() if k != "logging"},
        {"logging": logging_data},
    )
    return Box(result, default_box=True)
