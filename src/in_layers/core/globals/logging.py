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
    CoreNamespace,
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
    tcp_options = context.config[CoreNamespace.root.value].logging.get(
        "tcp_logging_options"
    )
    if not tcp_options:
        raise ValueError("Must include tcp_logging_options when using a tcp logger")
    url = tcp_options["url"]
    headers = tcp_options.get("headers", {})
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

        class _HL(HighLevelLogger):  # type: ignore[misc]
            def get_app_logger(self, app_name: str) -> AppLogger:
                return _app_logger(context, sub, app_name)

            # Logger methods forwarded
            def trace(self, *a, **k):
                return sub.trace(*a, **k)

            def debug(self, *a, **k):
                return sub.debug(*a, **k)

            def info(self, *a, **k):
                return sub.info(*a, **k)

            def warn(self, *a, **k):
                return sub.warn(*a, **k)

            def error(self, *a, **k):
                return sub.error(*a, **k)

            def apply_data(self, *a, **k):
                return sub.apply_data(*a, **k)

            def get_id_logger(self, *a, **k):
                return sub.get_id_logger(*a, **k)

            def get_sub_logger(self, *a, **k):
                return sub.get_sub_logger(*a, **k)

            def get_ids(self):
                return sub.get_ids()

        return _HL()

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
            logging_cfg = context.config[CoreNamespace.root.value].logging
            custom = logging_cfg.get("custom_logger")
            if custom:
                ids = _get_ids_with_runtime(context.constants["runtime_id"], props)
                return custom.get_logger(context, {**(props or {}), "ids": ids})
            methods = _get_log_methods_from_format(logging_cfg["log_format"])
            return composite_logger(methods).get_logger(context, props)

    return _Root()


def _sub_logger(
    context: CommonContext,
    log_methods: list[LogMethod],
    props: dict[str, Any],
) -> Logger:
    config_level: LogLevelNames = context.config[CoreNamespace.root.value].logging[
        "log_level"
    ]
    bound_methods: list[Callable[[LogMessage], Any]] = [m(context) for m in log_methods]

    def _do_log(message_level: LogLevelNames):
        def _f(
            message: str,
            data_or_error: Mapping[str, Any] | None = None,
            *,
            ignore_size_limit: bool = False,
        ) -> Any:
            if _should_ignore(config_level, message_level):
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
                    context.config[CoreNamespace.root.value].logging.get(
                        "max_log_size_in_characters", 50000
                    ),
                )
            )
            log_message: LogMessage = {
                "id": str(uuid.uuid4()),
                "environment": context.constants["environment"],
                "datetime": datetime.now(tz=UTC),
                "log_level": message_level,
                "message": message,
                "ids": props.get("ids"),
                "logger": ":".join(props.get("names", [])),
                **({"error": data_or_error["error"]} if is_error else {}),
                **the_data,
            }  # type: ignore[typeddict-item]
            [bm(log_message) for bm in bound_methods]
            return None

        return _f

    class _Logger(Logger):  # type: ignore[misc]
        def get_ids(self) -> list[LogId]:
            return list(props.get("ids") or [])

        def debug(self, *a, **k):
            return _do_log(LogLevelNames.debug)(*a, **k)

        def info(self, *a, **k):
            return _do_log(LogLevelNames.info)(*a, **k)

        def warn(self, *a, **k):
            return _do_log(LogLevelNames.warn)(*a, **k)

        def trace(self, *a, **k):
            return _do_log(LogLevelNames.debug)(*a, **k)

        def error(self, *a, **k):
            return _do_log(LogLevelNames.error)(*a, **k)

        def get_sub_logger(self, name: str) -> Logger:
            return _sub_logger(
                context,
                log_methods,
                {**props, "names": [*props.get("names", []), name]},
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
            ids = [*props.get("ids", []), log_id]
            return _sub_logger(
                context,
                log_methods,
                {**props, "names": [*props.get("names", []), name], "ids": ids},
            )

        def apply_data(self, data: Mapping[str, Any]) -> Logger:
            merged = dict(props)
            merged.update(data)
            if "ids" not in data:
                merged["ids"] = props.get("ids")
            return _sub_logger(context, log_methods, merged)

    return _Logger()


def _app_logger(context: CommonContext, sub_logger: Logger, app_name: str) -> AppLogger:
    the_logger = sub_logger.get_sub_logger(app_name).apply_data({"app": app_name})

    class _AL(AppLogger):  # type: ignore[misc]
        def get_layer_logger(
            self,
            layer_name: CommonLayerName | str,
            cross_layer_props: CrossLayerProps | None = None,
        ) -> LayerLogger:
            return _layer_logger(
                context, the_logger, str(layer_name), cross_layer_props
            )

        def trace(self, *a, **k):
            return the_logger.trace(*a, **k)

        def debug(self, *a, **k):
            return the_logger.debug(*a, **k)

        def info(self, *a, **k):
            return the_logger.info(*a, **k)

        def warn(self, *a, **k):
            return the_logger.warn(*a, **k)

        def error(self, *a, **k):
            return the_logger.error(*a, **k)

        def apply_data(self, *a, **k):
            return the_logger.apply_data(*a, **k)

        def get_id_logger(self, *a, **k):
            return the_logger.get_id_logger(*a, **k)

        def get_sub_logger(self, *a, **k):
            return the_logger.get_sub_logger(*a, **k)

        def get_ids(self):
            return the_logger.get_ids()

    return _AL()


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

    def get_function_logger(
        function_name: str, cross: CrossLayerProps | None = None
    ) -> Logger:
        func_logger = the_logger.get_id_logger(
            function_name, "function_call_id", str(uuid.uuid4())
        ).apply_data({"function": function_name})
        combined = combine_cross_layer_props(
            {"logging": {"ids": func_logger.get_ids()}},
            cross or {"logging": {"ids": []}},
        )
        return func_logger.apply_data(combined["logging"])

    def get_inner_logger(
        function_name: str, cross: CrossLayerProps | None = None
    ) -> Logger:
        func_logger = the_logger.get_sub_logger(function_name).apply_data(
            {"function": function_name}
        )
        combined = combine_cross_layer_props(
            {"logging": {"ids": func_logger.get_ids()}},
            cross or {"logging": {"ids": []}},
        )
        return func_logger.apply_data(combined["logging"])

    def _log_wrap(function_name: str, func: Callable[..., Any]) -> Callable[..., Any]:
        layer = str(layer_name)

        def _wrapped(*args: Any, **kwargs: Any) -> Any:  # noqa: ARG001
            args_no_cross, cross = extract_cross_layer_props(list(args))
            flog = get_function_logger(function_name, cross)
            level = _get_wrap_level(context, layer, function_name)
            getattr(flog, level)(f"Executing {layer} function", {"args": args_no_cross})
            try:
                result = func(
                    flog, *args_no_cross, {"logging": {"ids": flog.get_ids()}}
                )
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

    def _wrap_async(function_name: str, func: Callable[..., Any]) -> Callable[..., Any]:
        return _log_wrap(function_name, func)

    def _wrap_sync(function_name: str, func: Callable[..., Any]) -> Callable[..., Any]:
        return _log_wrap(function_name, func)

    class _LL(LayerLogger):  # type: ignore[misc]
        def get_function_logger(
            self, name: str, cross_layer_props: CrossLayerProps | None = None
        ) -> Logger:
            return get_function_logger(name, cross_layer_props)

        def get_inner_logger(
            self, function_name: str, cross_layer_props: CrossLayerProps | None = None
        ) -> Logger:
            return get_inner_logger(function_name, cross_layer_props)

        def _log_wrap(
            self, function_name: str, func: Callable[..., Any]
        ) -> Callable[..., Any]:
            return _log_wrap(function_name, func)

        def _log_wrap_async(
            self, function_name: str, func: Callable[..., Any]
        ) -> Callable[..., Any]:
            return _wrap_async(function_name, func)

        def _log_wrap_sync(
            self, function_name: str, func: Callable[..., Any]
        ) -> Callable[..., Any]:
            return _wrap_sync(function_name, func)

        def trace(self, *a, **k):
            return the_logger.trace(*a, **k)

        def debug(self, *a, **k):
            return the_logger.debug(*a, **k)

        def info(self, *a, **k):
            return the_logger.info(*a, **k)

        def warn(self, *a, **k):
            return the_logger.warn(*a, **k)

        def error(self, *a, **k):
            return the_logger.error(*a, **k)

        def apply_data(self, *a, **k):
            return the_logger.apply_data(*a, **k)

        def get_id_logger(self, *a, **k):
            return the_logger.get_id_logger(*a, **k)

        def get_sub_logger(self, *a, **k):
            return the_logger.get_sub_logger(*a, **k)

        def get_ids(self):
            return the_logger.get_ids()

    return _LL()


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
    getter = (
        context["config"][CoreNamespace.root.value]["logging"].get(
            "get_function_wrap_log_level"
        )
        if context
        and "config" in context
        and context["config"].get(CoreNamespace.root.value)
        else None
    )
    level = (
        getter(layer_name, function_name)
        if callable(getter)
        else default_get_function_wrap_log_level(layer_name)
    )
    return level.value if isinstance(level, LogLevelNames) else str(level)
