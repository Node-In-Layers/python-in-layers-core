from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from ..libs import (
    combine_cross_layer_props,
    create_error_object,
    get_log_level_number,
)
from ..protocols import (
    AppLogger,
    CommonContext,
    CommonLayerName,
    CrossLayerProps,
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
            data_or_error: Mapping[str, Any] | None = None,
            *,
            ignore_size_limit: bool = False,
        ) -> Any:
            if _should_ignore(self.config_level, message_level):
                return None
            is_error = isinstance(data_or_error, Mapping) and "error" in (
                data_or_error or {}
            )
            data = {} if is_error else dict(data_or_error or {})
            the_data = (
                data
                if ignore_size_limit
                else cap_for_logging(
                    data,
                    self.context.config.in_layers_core.logging.get(
                        "max_log_size_in_characters", DEFAULT_MAX_LOG_SIZE_IN_CHARACTERS
                    )
                    or 50000,
                )
            )
            log_message: LogMessage = {
                "id": str(uuid.uuid4()),
                "environment": self.context.constants["environment"],
                "datetime": datetime.now(tz=UTC),
                "log_level": message_level,
                "message": message,
                "ids": self.props.get("ids"),
                "logger": ":".join(self.props.get("names", [])),
                **({"error": data_or_error["error"]} if is_error else {}),
                **the_data,
            }  # type: ignore[typeddict-item]
            [bm(log_message) for bm in self.bound_methods]
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
        return self._do_log(LogLevelNames.debug)(*a, **k)

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


class _AppLoggerImpl(AppLogger):  # type: ignore[misc]
    def __init__(self, ctx: CommonContext, base_logger: Logger):
        self._ctx = ctx
        self._base = base_logger

    def get_layer_logger(
        self,
        layer_name: CommonLayerName | str,
        cross_layer_props: CrossLayerProps | None = None,
    ) -> LayerLogger:
        return _layer_logger(self._ctx, self._base, str(layer_name), cross_layer_props)

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
    return _AppLoggerImpl(context, the_logger)


class _LayerLoggerImpl(LayerLogger):  # type: ignore[misc]
    def __init__(self, ctx: CommonContext, lname: str, base: Logger):
        self._ctx = ctx
        self._layer = lname
        self._base = base

    def get_function_logger(
        self, name: str, cross_layer_props: CrossLayerProps | None = None
    ) -> Logger:
        func_logger = self._base.get_id_logger(
            name, "function_call_id", str(uuid.uuid4())
        ).apply_data({"function": name})
        combined = combine_cross_layer_props(
            {"logging": {"ids": func_logger.get_ids()}},
            cross_layer_props or {"logging": {"ids": []}},
        )
        return func_logger.apply_data(combined["logging"])

    def get_inner_logger(
        self, function_name: str, cross_layer_props: CrossLayerProps | None = None
    ) -> Logger:
        func_logger = self._base.get_sub_logger(function_name).apply_data(
            {"function": function_name}
        )
        combined = combine_cross_layer_props(
            {"logging": {"ids": func_logger.get_ids()}},
            cross_layer_props or {"logging": {"ids": []}},
        )
        return func_logger.apply_data(combined["logging"])

    def _log_wrap(
        self, function_name: str, func: Callable[..., Any]
    ) -> Callable[..., Any]:
        layer = self._layer

        def _wrapped(*args: Any, **kwargs: Any) -> Any:
            # Extract cross-layer props from positional or keyword args
            args_no_cross, kwargs_no_cross, cross_layer_props = (
                extract_cross_layer_props(list(args), dict(kwargs))
            )
            flog = self.get_function_logger(function_name, cross_layer_props)
            level = _get_wrap_level(self._ctx, layer, function_name)
            getattr(flog, level)(f"Executing {layer} function", {"args": args_no_cross})
            try:
                # Always provide the combined logging ids to the inner function wrapper
                combined = combine_cross_layer_props(
                    {"logging": {"ids": flog.get_ids()}}, cross_layer_props or {}
                )
                # Pass combined cross to inner wrapper via keyword; it will decide final forwarding
                result = func(flog, *args_no_cross, **kwargs_no_cross, cross_layer_props=combined)  # type: ignore[arg-type]
                getattr(flog, level)(f"Executed {layer} function", {"result": result})
                return result
            except Exception as e:
                flog.error(
                    "Function failed with an exception",
                    create_error_object(
                        "INTERNAL_ERROR", f"Layer function {layer}:{function_name}", e
                    ),
                )
                raise

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
    layer_name: CommonLayerName | str,
    cross_layer_props: CrossLayerProps | None = None,
) -> LayerLogger:
    inner = sub_logger.get_sub_logger(str(layer_name)).apply_data(
        {"layer": str(layer_name)}
    )
    the_logger = inner.apply_data(combine_logging_props(inner, cross_layer_props))
    return _LayerLoggerImpl(context, str(layer_name), the_logger)


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
