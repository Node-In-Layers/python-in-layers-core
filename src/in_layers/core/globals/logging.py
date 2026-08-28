from __future__ import annotations

import base64
import json
import logging
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import httpx
from pydantic import RootModel

from ..libs import (
    create_cross_layer_props,
    create_error_object,
    get_log_level_number,
    get_otel_forward_baggage_from_config,
)
from ..otel.libs import create_otel_log_method
from ..protocols import (
    AppLogger,
    CommonContext,
    CommonLayerName,
    CoreNamespace,
    CrossLayerProps,
    ErrorObject,
    FunctionLogger,
    HighLevelLogger,
    LayerLogger,
    LogFormat,
    Logger,
    LogId,
    LogLevel,
    LogLevelNames,
    LogMessage,
    LogMethod,
    RootLogger,
)
from .libs import (
    cap_for_logging,
    combine_logging_props,
    default_get_function_wrap_log_level,
    extract_cross_layer_props,
)

MAX_LOGGING_ATTEMPTS = 5
DEFAULT_MAX_LOG_SIZE_IN_CHARACTERS = 50000


def _log_sink_failure(error: Exception) -> None:
    message = str(error)
    try:
        sys.stderr.write(f"[in-layers-core] log sink failed: {message}\n")
    except Exception:
        logging.exception("Log sink failure could not be written to stderr")


def _get_mapping_value(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _get_override_flag(cross_layer_props: CrossLayerProps | None, key: str) -> bool:
    if not cross_layer_props:
        return False
    logging_data = _get_mapping_value(cross_layer_props, "logging")
    overrides = _get_mapping_value(logging_data, "overrides")
    if overrides is None:
        return False
    direct = _get_mapping_value(overrides, key)
    if direct is not None:
        return bool(direct)
    camel = "".join(
        [
            part if index == 0 else part.capitalize()
            for index, part in enumerate(key.split("_"))
        ]
    )
    camel_value = _get_mapping_value(overrides, camel)
    return bool(camel_value)


def _handle_special_types(
    obj: Any, max_depth: int, _depth: int, _seen: set[int]
) -> tuple[bool, Any]:
    """Handle Enum, datetime, and bytes types. Returns (handled, value)."""
    if isinstance(obj, Enum):
        return True, _to_jsonable(
            obj.value, max_depth=max_depth, _depth=_depth + 1, _seen=_seen
        )
    if isinstance(obj, datetime):
        return True, obj.isoformat()
    if isinstance(obj, (bytes, bytearray, memoryview)):
        b = bytes(obj)
        try:
            return True, b.decode("utf-8")
        except Exception:
            return True, {"$bytes_b64": base64.b64encode(b).decode("ascii")}
    return False, None


def _handle_structured_types(
    obj: Any, max_depth: int, _depth: int, _seen: set[int]
) -> tuple[bool, Any]:
    """Handle Pydantic models, dataclasses, and exceptions. Returns (handled, value)."""
    if hasattr(obj, "model_dump") and callable(obj.model_dump):
        try:
            dumped = obj.model_dump(mode="json")  # type: ignore[call-arg]
        except Exception:
            dumped = obj.model_dump()  # type: ignore[call-arg]
        return True, _to_jsonable(
            dumped, max_depth=max_depth, _depth=_depth + 1, _seen=_seen
        )
    if is_dataclass(obj):
        return True, _to_jsonable(
            asdict(obj), max_depth=max_depth, _depth=_depth + 1, _seen=_seen
        )
    if isinstance(obj, BaseException):
        return True, {
            "type": type(obj).__name__,
            "message": str(obj),
            "args": _to_jsonable(
                list(getattr(obj, "args", ()) or ()),
                max_depth=max_depth,
                _depth=_depth + 1,
                _seen=_seen,
            ),
        }
    return False, None


def _handle_collections(
    obj: Any, max_depth: int, _depth: int, _seen: set[int]
) -> tuple[bool, Any]:
    """Handle mappings and sequences. Returns (handled, value)."""
    if isinstance(obj, Mapping):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            out[str(k)] = _to_jsonable(
                v, max_depth=max_depth, _depth=_depth + 1, _seen=_seen
            )
        return True, out
    if isinstance(obj, (list, tuple, set, frozenset)):
        return True, [
            _to_jsonable(x, max_depth=max_depth, _depth=_depth + 1, _seen=_seen)
            for x in list(obj)
        ]
    return False, None


def _to_jsonable(
    obj: Any,
    *,
    max_depth: int = 6,
    _depth: int = 0,
    _seen: set[int] | None = None,
) -> Any:
    """
    Convert arbitrary objects into JSON-serializable structures.

    This is intentionally lossy: anything unknown becomes a string.
    """
    if _seen is None:
        _seen = set()
    if _depth > max_depth:
        return "[MaxDepth]"

    # Fast-path JSON primitives
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj

    # Avoid cycles
    oid = id(obj)
    if oid in _seen:
        return "[Circular]"
    _seen.add(oid)

    handlers = [
        _handle_special_types,
        _handle_structured_types,
        _handle_collections,
    ]
    try:
        for handler in handlers:
            handled, value = handler(obj, max_depth, _depth, _seen)
            if handled:
                return value
        # As a last resort, prefer stringifying (stable + safe)
        return str(obj)
    finally:
        # allow the same object to appear elsewhere without being treated as a cycle
        _seen.discard(oid)


def _setup_python_logging(level: int) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter("%(message)s")
    for h in root.handlers:
        h.setFormatter(fmt)

    if not root.handlers:
        h = logging.StreamHandler()
        h.setLevel(level)
        h.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(h)


def _to_std_level(name: LogLevelNames | None) -> int:
    if name == LogLevelNames.error:
        return logging.ERROR
    if name == LogLevelNames.warn:
        return logging.WARNING
    if name == LogLevelNames.info:
        return logging.INFO
    # Map both trace and debug to DEBUG for std logging
    return logging.DEBUG


def console_log_simple(log_message: LogMessage) -> None:
    splitted = (log_message.get("logger") or "root").split(":")
    function_name = splitted[-1] if splitted else "root"
    msg = f"{log_message['datetime'].isoformat()}: {function_name} {log_message['message']}"
    logging.log(_to_std_level(log_message.get("log_level")), msg)


def _combine_ids(ids: list[LogId]) -> str:
    parts: list[str] = []
    for obj in ids:
        parts.append(";".join([f"{k}:{v}" for k, v in obj.items()]))
    return ";".join(parts)


def console_log_full(log_message: LogMessage) -> None:
    ids = log_message.get("ids")
    level_obj = log_message.get("log_level")
    level_str = (
        level_obj.value
        if hasattr(level_obj, "value")
        else (str(level_obj) if level_obj is not None else "")
    )
    if ids:
        msg = (
            f"{log_message['datetime'].isoformat()} {log_message.get('environment')} {level_str} {log_message['id']} "
            f"[{log_message.get('logger')}] {{{_combine_ids(ids)}}} {log_message['message']}"
        )
    else:
        msg = (
            f"{log_message['datetime'].isoformat()} {log_message.get('environment')} {level_str} "
            f"[{log_message.get('logger')}] {log_message['message']}"
        )
    logging.log(_to_std_level(log_message.get("log_level")), msg)


def console_log_json(log_message: LogMessage) -> None:
    base = {
        "id": log_message.get("id"),
        "datetime": log_message["datetime"].isoformat(),
        "log_level": log_message.get("log_level"),
        "logger": log_message.get("logger"),
        "message": log_message.get("message"),
    }
    rest = dict(log_message)
    for k in ["id", "datetime", "log_level", "logger", "message"]:
        rest.pop(k, None)
    msg = json.dumps({**base, **rest}, default=str)
    logging.log(_to_std_level(log_message.get("log_level")), msg)


def log_tcp(context: CommonContext) -> Callable[[LogMessage], Any]:
    tcp_options = context.config.in_layers_core.logging.tcp_logging_options
    if not tcp_options:
        raise ValueError("Must include tcp_logging_options when using a tcp logger")
    url = tcp_options.url
    headers = tcp_options.headers or {}
    client = httpx.Client(base_url=url, headers=headers)

    def _send(log_message: LogMessage) -> Any:
        success: bool | None = None
        for _ in range(MAX_LOGGING_ATTEMPTS):
            try:
                client.post("", json=log_message)
                success = True
                break
            except Exception:
                logging.exception("Logging error")
                success = False
        return success

    return _send


def _should_ignore(config_level: LogLevelNames, message_level: LogLevelNames) -> bool:
    a = get_log_level_number(config_level)
    if a == LogLevel.SILENT.value:
        return True
    b = get_log_level_number(message_level)
    return a > b


def _get_log_methods_from_format(
    log_format: LogFormat | list[LogFormat],
) -> list[LogMethod]:
    if isinstance(log_format, list):
        result: list[LogMethod] = []
        for lf in log_format:
            result.extend(_get_log_methods_from_format(lf))
        return result
    if log_format == LogFormat.custom:
        raise ValueError(
            "This should never be here. custom_logger should override this"
        )
    if log_format == LogFormat.json:
        return [lambda _ctx: console_log_json]
    if log_format == LogFormat.simple:
        return [lambda _ctx: console_log_simple]
    if log_format == LogFormat.full:
        return [lambda _ctx: console_log_full]
    if log_format == LogFormat.tcp:
        return [log_tcp]
    if log_format == LogFormat.otel:
        return [create_otel_log_method()]
    raise ValueError(f"LogFormat {log_format} is not supported")


class _HL(HighLevelLogger):  # type: ignore[misc]
    def __init__(self, context: CommonContext, sub: Logger):
        self.context = context
        self.sub = sub

    def get_app_logger(self, app_name: str) -> AppLogger:
        return _app_logger(self.context, self.sub, app_name)

    # Logger methods forwarded
    def trace(self, *a, **k):
        return self.sub.trace(*a, **k)

    def debug(self, *a, **k):
        return self.sub.debug(*a, **k)

    def info(self, *a, **k):
        return self.sub.info(*a, **k)

    def warn(self, *a, **k):
        return self.sub.warn(*a, **k)

    def error(self, *a, **k):
        return self.sub.error(*a, **k)

    def apply_data(self, *a, **k):
        return self.sub.apply_data(*a, **k)

    def get_id_logger(self, *a, **k):
        return self.sub.get_id_logger(*a, **k)

    def get_sub_logger(self, *a, **k):
        return self.sub.get_sub_logger(*a, **k)

    def get_ids(self):
        return self.sub.get_ids()


def composite_logger(log_methods: Sequence[LogMethod]) -> RootLogger:
    def get_logger(
        context: CommonContext, props: Mapping[str, Any] | None = None
    ) -> HighLevelLogger:
        ids = _get_ids_with_runtime(context["constants"]["runtime_id"], props)
        sub = _sub_logger(
            context,
            list(log_methods),
            {"names": [], "ids": ids, "data": dict(props or {}).get("data", {})},
        )
        return _HL(context, sub)

    class _Root(RootLogger):  # type: ignore[misc]
        def get_logger(
            self, context: CommonContext, props: Mapping[str, Any] | None = None
        ) -> HighLevelLogger:
            return get_logger(context, props)

    return _Root()


def standard_logger() -> RootLogger:
    class _Root(RootLogger):  # type: ignore[misc]
        def get_logger(
            self, context: CommonContext, props: Mapping[str, Any] | None = None
        ) -> HighLevelLogger:
            _setup_python_logging(
                _to_std_level(context.config.in_layers_core.logging.log_level)
            )
            logging_cfg = context.config.in_layers_core.logging
            custom = logging_cfg.get("custom_logger", None)
            if custom:
                ids = _get_ids_with_runtime(context.constants.runtime_id, props)
                return custom.get_logger(context, {**(props or {}), "ids": ids})
            methods = _get_log_methods_from_format(logging_cfg.log_format)
            return composite_logger(methods).get_logger(context, props)

    return _Root()


class _Logger(Logger):  # type: ignore[misc]
    def __init__(
        self,
        context: CommonContext,
        log_methods: list[LogMethod],
        props: dict[str, Any],
    ):
        self.context = context
        self.props = props
        self.config_level: LogLevelNames = (
            context.config.in_layers_core.logging.log_level
        )
        self.bound_methods: list[Callable[[LogMessage], Any]] = [
            m(context) for m in log_methods
        ]
        self.log_methods = log_methods

    def _do_log(self, message_level: LogLevelNames):
        def _f(
            message: str,
            data_or_error: Mapping[str, Any] | ErrorObject | None = None,
            *,
            ignore_size_limit: bool = False,
        ) -> Any:
            if _should_ignore(self.config_level, message_level):
                return None
            is_error_obj = isinstance(data_or_error, ErrorObject)
            is_error_like = isinstance(data_or_error, Mapping) and "error" in (
                data_or_error or {}
            )
            data = {}
            if is_error_obj:
                data = RootModel(data_or_error).model_dump()
            elif is_error_like:
                data = dict(data_or_error or {})
            else:
                data = dict(data_or_error or {})
            # Ensure that logged payloads are JSON-serializable so consumers
            # (e.g. tcp logger / external pipelines) don't need to normalize.
            jsonable_data = _to_jsonable(data)
            error_jsonable = (
                jsonable_data.get("error")
                if isinstance(jsonable_data, Mapping)
                and (is_error_like or is_error_obj)
                else None
            )
            the_data = (
                jsonable_data
                if ignore_size_limit
                else cap_for_logging(
                    jsonable_data,
                    self.context.config.in_layers_core.logging.get(
                        "max_log_size_in_characters", DEFAULT_MAX_LOG_SIZE_IN_CHARACTERS
                    )
                    or 50000,
                )
            )
            if (is_error_like or is_error_obj) and error_jsonable is not None:
                # Preserve `error` even if truncation dropped it.
                try:
                    the_data = dict(the_data or {})
                    the_data["error"] = error_jsonable
                except Exception:
                    the_data = {"error": error_jsonable}
            log_message: LogMessage = {
                "id": str(uuid.uuid4()),
                "environment": self.context.constants["environment"],
                "datetime": datetime.now(tz=UTC),
                "log_level": message_level,
                "message": message,
                "ids": self.props.get("ids"),
                "logger": ":".join(self.props.get("names", [])),
                **the_data,
            }  # type: ignore[typeddict-item]
            for bound_method in self.bound_methods:
                try:
                    bound_method(log_message)
                except Exception as error:
                    _log_sink_failure(error)
            return None

        return _f

    def get_ids(self) -> list[LogId]:
        return list(self.props.get("ids") or [])

    def debug(self, *a, **k):
        return self._do_log(LogLevelNames.debug)(*a, **k)

    def info(self, *a, **k):
        return self._do_log(LogLevelNames.info)(*a, **k)

    def warn(self, *a, **k):
        return self._do_log(LogLevelNames.warn)(*a, **k)

    def trace(self, *a, **k):
        return self._do_log(LogLevelNames.trace)(*a, **k)

    def error(self, *a, **k):
        return self._do_log(LogLevelNames.error)(*a, **k)

    def get_sub_logger(self, name: str) -> Logger:
        return _sub_logger(
            self.context,
            self.log_methods,
            {**self.props, "names": [*self.props.get("names", []), name]},
        )

    def get_id_logger(
        self, name: str, log_id_or_key: LogId | str, id: str | None = None
    ) -> Logger:
        if not isinstance(log_id_or_key, Mapping) and not id:
            raise ValueError("Need value if providing a key")
        log_id: LogId = (
            log_id_or_key
            if isinstance(log_id_or_key, Mapping)
            else {str(log_id_or_key): str(id or "")}
        )
        ids = [*self.props.get("ids", []), log_id]
        return _sub_logger(
            self.context,
            self.log_methods,
            {**self.props, "names": [*self.props.get("names", []), name], "ids": ids},
        )

    def apply_data(self, data: Mapping[str, Any]) -> Logger:
        merged = dict(self.props)
        merged.update(data)
        if "ids" not in data:
            merged["ids"] = self.props.get("ids")
        return _sub_logger(self.context, self.log_methods, merged)


def _sub_logger(
    context: CommonContext,
    log_methods: list[LogMethod],
    props: dict[str, Any],
) -> Logger:
    return _Logger(context, log_methods, props)


def _run_function_wrap(
    context: CommonContext,
    *,
    domain: str,
    layer: str,
    function_name: str,
    func_logger: Logger,
    omit_wrap_payload: bool,
    args_for_logging: list[Any],
    execute: Callable[[], Any],
) -> Any:
    level = _get_wrap_level(context, layer, function_name)

    def _do_work() -> Any:
        getattr(func_logger, level)(
            f"Executing {layer} function",
            {} if omit_wrap_payload else {"args": args_for_logging},
        )
        try:
            result = execute()
            getattr(func_logger, level)(
                f"Executed {layer} function",
                {} if omit_wrap_payload else {"result": result},
            )
            return result
        except Exception as error:
            func_logger.error(  # noqa: PLE1205
                "Function failed with an exception",
                create_error_object(
                    "INTERNAL_ERROR",
                    f"Layer function {layer}:{function_name}",
                    error,
                ),
            )
            raise

    services = _get_mapping_value(context, "services") or {}
    otel = _get_mapping_value(services, CoreNamespace.otel.value)
    run_with_trace_and_metrics = _get_mapping_value(otel, "run_with_trace_and_metrics")
    if callable(run_with_trace_and_metrics):
        return run_with_trace_and_metrics(
            {
                "layer_name": layer,
                "domain": domain,
                "function_name": function_name,
                "get_ids": func_logger.get_ids,
                "wrap_span_events": {
                    "args": args_for_logging,
                    "omit_data": omit_wrap_payload,
                    "record_result": True,
                },
            },
            _do_work,
        )
    return _do_work()


def _create_scoped_logger_core(
    base_logger: Logger,
    scope_name: str,
    cross_layer_props: CrossLayerProps | None,
    create_function_call_id: bool,
) -> Logger:
    logger = (
        base_logger.get_id_logger(
            scope_name,
            "function_call_id",
            str(uuid.uuid4()),
        )
        if create_function_call_id
        else base_logger.get_sub_logger(scope_name)
    ).apply_data({"function": scope_name})
    return logger.apply_data(combine_logging_props(logger, cross_layer_props))


def _normalize_wrap_step_args(
    name_or_fn: str | Callable[[], Any],
    fn_or_options: Callable[[], Any] | Mapping[str, Any] | None = None,
    options: Mapping[str, Any] | None = None,
) -> tuple[str | None, Callable[[], Any], Mapping[str, Any]]:
    if callable(name_or_fn):
        return None, name_or_fn, dict(fn_or_options or {})
    return str(name_or_fn), fn_or_options, dict(options or {})  # type: ignore[return-value]


class _FunctionLoggerImpl(FunctionLogger):  # type: ignore[misc]
    def __init__(
        self,
        ctx: CommonContext,
        domain: str,
        layer: str,
        function_name: str,
        base_parent: Logger,
        template_cross_layer_props: CrossLayerProps | None = None,
        create_function_call_id: bool = True,
    ):
        self._ctx = ctx
        self._domain = domain
        self._layer = layer
        self._function_name = function_name
        self._base_parent = base_parent
        self._template_cross_layer_props = template_cross_layer_props
        self._create_function_call_id = create_function_call_id
        self._template_logger = self._build_scope_logger(template_cross_layer_props)

    def _build_scope_logger(
        self, cross_layer_props: CrossLayerProps | None = None
    ) -> Logger:
        return _create_scoped_logger_core(
            self._base_parent,
            self._function_name,
            cross_layer_props or self._template_cross_layer_props,
            self._create_function_call_id,
        )

    def _build_inner_logger(
        self, name: str, cross_layer_props: CrossLayerProps | None = None
    ) -> FunctionLogger:
        return _FunctionLoggerImpl(
            self._ctx,
            self._domain,
            self._layer,
            name,
            self._template_logger,
            cross_layer_props,
            False,
        )

    def wrap(
        self,
        fn: Callable[[], Any],
        options: Mapping[str, Any] | None = None,
    ) -> Any:
        return self.wrap_step(fn, options)

    def wrap_step(
        self,
        name_or_fn: str | Callable[[], Any],
        fn_or_options: Callable[[], Any] | Mapping[str, Any] | None = None,
        options: Mapping[str, Any] | None = None,
    ) -> Any:
        step_name, fn, options_map = _normalize_wrap_step_args(
            name_or_fn,
            fn_or_options,
            options,
        )
        if step_name is not None:
            inner_logger = self._build_inner_logger(
                step_name,
                options_map.get("cross_layer_props")
                or options_map.get("crossLayerProps"),
            )
            next_options = (
                {"args": list(options_map.get("args", []))}
                if "args" in options_map
                else None
            )
            return inner_logger.wrap_step(fn, next_options)

        cross_layer_props = options_map.get("cross_layer_props")
        if cross_layer_props is None:
            cross_layer_props = options_map.get("crossLayerProps")
        args_for_logging = list(options_map.get("args", []))
        func_logger = (
            self._template_logger.apply_data(
                combine_logging_props(self._template_logger, cross_layer_props)
            )
            if cross_layer_props is not None
            else self._template_logger
        )
        wrap_cross_layer = cross_layer_props or self._template_cross_layer_props
        omit_wrap_payload = _get_override_flag(wrap_cross_layer, "omit_data")
        return _run_function_wrap(
            self._ctx,
            domain=self._domain,
            layer=self._layer,
            function_name=step_name or self._function_name,
            func_logger=func_logger,
            omit_wrap_payload=omit_wrap_payload,
            args_for_logging=args_for_logging,
            execute=fn,
        )

    def wrap_function_call(
        self,
        function_name: str,
        fn: Callable[..., Any],
        options: Mapping[str, Any] | None = None,
    ) -> Any:
        options_map = dict(options or {})
        cross_layer_props = options_map.get("cross_layer_props")
        if cross_layer_props is None:
            cross_layer_props = options_map.get("crossLayerProps")
        args_for_logging = list(options_map.get("args", []))
        function_logger = self.get_function_logger(function_name, cross_layer_props)
        return function_logger.wrap_step(
            lambda: fn(function_logger, *args_for_logging),
            {
                "args": args_for_logging,
            },
        )

    def get_function_logger(
        self,
        name: str,
        cross_layer_props: CrossLayerProps | None = None,
    ) -> FunctionLogger:
        return _FunctionLoggerImpl(
            self._ctx,
            self._domain,
            self._layer,
            name,
            self._template_logger,
            cross_layer_props,
            True,
        )

    def trace(self, *a, **k):
        return self._template_logger.trace(*a, **k)

    def debug(self, *a, **k):
        return self._template_logger.debug(*a, **k)

    def info(self, *a, **k):
        return self._template_logger.info(*a, **k)

    def warn(self, *a, **k):
        return self._template_logger.warn(*a, **k)

    def error(self, *a, **k):
        return self._template_logger.error(*a, **k)

    def apply_data(self, *a, **k):
        return self._template_logger.apply_data(*a, **k)

    def get_id_logger(self, *a, **k):
        return self._template_logger.get_id_logger(*a, **k)

    def get_sub_logger(self, *a, **k):
        return self._template_logger.get_sub_logger(*a, **k)

    def get_ids(self):
        return self._template_logger.get_ids()


class _AppLoggerImpl(AppLogger):  # type: ignore[misc]
    def __init__(self, ctx: CommonContext, app_name: str, base_logger: Logger):
        self._ctx = ctx
        self._app_name = app_name
        self._base = base_logger

    def get_layer_logger(
        self,
        layer_name: CommonLayerName | str,
        cross_layer_props: CrossLayerProps | None = None,
    ) -> LayerLogger:
        return _layer_logger(
            self._ctx,
            self._base,
            self._app_name,
            str(layer_name),
            cross_layer_props,
        )

    def trace(self, *a, **k):
        return self._base.trace(*a, **k)

    def debug(self, *a, **k):
        return self._base.debug(*a, **k)

    def info(self, *a, **k):
        return self._base.info(*a, **k)

    def warn(self, *a, **k):
        return self._base.warn(*a, **k)

    def error(self, *a, **k):
        return self._base.error(*a, **k)

    def apply_data(self, *a, **k):
        return self._base.apply_data(*a, **k)

    def get_id_logger(self, *a, **k):
        return self._base.get_id_logger(*a, **k)

    def get_sub_logger(self, *a, **k):
        return self._base.get_sub_logger(*a, **k)

    def get_ids(self):
        return self._base.get_ids()


def _app_logger(context: CommonContext, sub_logger: Logger, app_name: str) -> AppLogger:
    the_logger = sub_logger.get_sub_logger(app_name).apply_data({"app": app_name})
    return _AppLoggerImpl(context, app_name, the_logger)


class _LayerLoggerImpl(LayerLogger):  # type: ignore[misc]
    def __init__(self, ctx: CommonContext, domain: str, lname: str, base: Logger):
        self._ctx = ctx
        self._domain = domain
        self._layer = lname
        self._base = base

    def _build_function_logger(
        self, name: str, cross_layer_props: CrossLayerProps | None = None
    ) -> _FunctionLoggerImpl:
        return _FunctionLoggerImpl(
            self._ctx,
            self._domain,
            self._layer,
            name,
            self._base,
            cross_layer_props,
            True,
        )

    def get_inner_logger(
        self, function_name: str, cross_layer_props: CrossLayerProps | None = None
    ) -> FunctionLogger:
        return _FunctionLoggerImpl(
            self._ctx,
            self._domain,
            self._layer,
            function_name,
            self._base,
            cross_layer_props,
            False,
        )

    def _log_wrap(
        self, function_name: str, func: Callable[..., Any]
    ) -> Callable[..., Any]:
        layer = self._layer

        def _wrapped(*args: Any, **kwargs: Any) -> Any:
            # Extract cross-layer props from positional or keyword args
            args_no_cross, kwargs_no_cross, cross_layer_props = (
                extract_cross_layer_props(list(args), dict(kwargs))
            )
            flog = self._build_function_logger(function_name, cross_layer_props)
            options = get_otel_forward_baggage_from_config(self._ctx.config)
            combined = create_cross_layer_props(flog, cross_layer_props, options)
            omit_wrap_payload = _get_override_flag(cross_layer_props, "omit_data")
            return _run_function_wrap(
                self._ctx,
                domain=self._domain,
                layer=layer,
                function_name=function_name,
                func_logger=flog,
                omit_wrap_payload=omit_wrap_payload,
                args_for_logging=args_no_cross,
                execute=lambda: func(
                    flog,
                    *args_no_cross,
                    **kwargs_no_cross,
                    cross_layer_props=combined,
                ),
            )

        return _wrapped

    def _log_wrap_async(
        self, function_name: str, func: Callable[..., Any]
    ) -> Callable[..., Any]:
        return self._log_wrap(function_name, func)

    def _log_wrap_sync(
        self, function_name: str, func: Callable[..., Any]
    ) -> Callable[..., Any]:
        return self._log_wrap(function_name, func)

    def trace(self, *a, **k):
        return self._base.trace(*a, **k)

    def debug(self, *a, **k):
        return self._base.debug(*a, **k)

    def info(self, *a, **k):
        return self._base.info(*a, **k)

    def warn(self, *a, **k):
        return self._base.warn(*a, **k)

    def error(self, *a, **k):
        return self._base.error(*a, **k)

    def apply_data(self, *a, **k):
        return self._base.apply_data(*a, **k)

    def get_id_logger(self, *a, **k):
        return self._base.get_id_logger(*a, **k)

    def get_sub_logger(self, *a, **k):
        return self._base.get_sub_logger(*a, **k)

    def get_ids(self):
        return self._base.get_ids()


def _layer_logger(
    context: CommonContext,
    sub_logger: Logger,
    domain_name: str,
    layer_name: CommonLayerName | str,
    cross_layer_props: CrossLayerProps | None = None,
) -> LayerLogger:
    inner = sub_logger.get_sub_logger(str(layer_name)).apply_data(
        {"layer": str(layer_name)}
    )
    the_logger = inner.apply_data(combine_logging_props(inner, cross_layer_props))
    return _LayerLoggerImpl(context, domain_name, str(layer_name), the_logger)


def _get_ids_with_runtime(
    runtime_id: str, props: Mapping[str, Any] | None
) -> list[LogId]:
    base: list[LogId] = [{"runtime_id": runtime_id}]
    if not props or "ids" not in props or not props.get("ids"):
        return base
    ids: list[LogId] = list(props.get("ids") or [])
    has_runtime = any("runtime_id" in x for x in ids)
    return ids if has_runtime else base + ids


def _get_wrap_level(
    context: CommonContext, layer_name: str, function_name: str | None
) -> str:
    getter = context.config.in_layers_core.logging.get(
        "get_function_wrap_log_level", None
    )
    level = (
        getter(layer_name, function_name)
        if callable(getter)
        else default_get_function_wrap_log_level(layer_name)
    )
    return level.value if isinstance(level, LogLevelNames) else str(level)
