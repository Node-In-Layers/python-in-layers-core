from __future__ import annotations

import importlib
import time
from collections.abc import Callable, Mapping
from typing import Any

from ..globals.libs import cap_for_logging
from ..protocols import CommonContext
from .libs import (
    create_noop_counter,
    create_noop_histogram,
    create_noop_span,
    layer_metric_attrs,
    layer_span_name,
    span_attributes_from_ids,
    span_kind_for_layer,
)

_SPAN_STATUS_ERROR = 2


class _WrappedSpan:
    def __init__(self, raw_span: Any):
        self._raw_span = raw_span

    def end(self) -> None:
        end = getattr(self._raw_span, "end", None)
        if callable(end):
            end()

    def add_event(self, name: str, attributes: Mapping[str, Any] | None = None) -> None:
        add_event = getattr(self._raw_span, "add_event", None)
        if callable(add_event):
            add_event(name, attributes=attributes)

    def set_attribute(self, key: str, value: Any) -> None:
        set_attribute = getattr(self._raw_span, "set_attribute", None)
        if callable(set_attribute):
            set_attribute(key, value)

    def set_status(self, status: Mapping[str, Any]) -> None:
        set_status = getattr(self._raw_span, "set_status", None)
        if callable(set_status):
            set_status(status)

    def span_context(self):
        getter = getattr(self._raw_span, "get_span_context", None)
        if not callable(getter):
            return None
        raw_context = getter()
        trace_id = getattr(raw_context, "trace_id", None)
        span_id = getattr(raw_context, "span_id", None)
        if trace_id in (None, 0) or span_id in (None, 0):
            return None
        return {
            "trace_id": (
                format(trace_id, "032x") if isinstance(trace_id, int) else str(trace_id)
            ),
            "span_id": (
                format(span_id, "016x") if isinstance(span_id, int) else str(span_id)
            ),
        }


class _TraceService:
    def __init__(self, root: _OtelRuntime):
        self._root = root

    def start_span(self, name: str, options: Mapping[str, Any] | None = None):
        tracer = self._root.get_tracer()
        if tracer is None:
            return create_noop_span()
        raw_span = tracer.start_span(
            name,
            attributes=dict((options or {}).get("attributes") or {}),
            kind=(options or {}).get("kind", span_kind_for_layer("models")),
        )
        return _WrappedSpan(raw_span)

    def run_with_span(
        self,
        name: str,
        fn: Callable[[Any], Any],
        options: Mapping[str, Any] | None = None,
    ) -> Any:
        tracer = self._root.get_tracer()
        if tracer is None:
            return fn(create_noop_span())
        kind = (options or {}).get("kind", span_kind_for_layer("models"))
        attributes = dict((options or {}).get("attributes") or {})
        start_as_current_span = getattr(tracer, "start_as_current_span", None)
        if callable(start_as_current_span):
            with start_as_current_span(
                name, attributes=attributes, kind=kind
            ) as raw_span:
                return fn(_WrappedSpan(raw_span))
        raw_span = tracer.start_span(name, attributes=attributes, kind=kind)
        wrapped = _WrappedSpan(raw_span)
        try:
            return fn(wrapped)
        except Exception as error:
            wrapped.set_status(
                {
                    "code": _SPAN_STATUS_ERROR,
                    "message": str(error),
                }
            )
            raise
        finally:
            wrapped.end()

    def get_active_span(self):
        trace_api = self._root.modules.get("trace")
        if not trace_api or not self._root.is_trace_enabled():
            return None
        get_current_span = getattr(trace_api, "get_current_span", None)
        if not callable(get_current_span):
            return None
        raw_span = get_current_span()
        wrapped = _WrappedSpan(raw_span)
        return wrapped if wrapped.span_context() else None


class _MetricsService:
    def __init__(self, root: _OtelRuntime):
        self._root = root

    def record_duration(
        self,
        name: str,
        duration_ms: int | float,
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        histogram = self.create_histogram(name, {"unit": "ms"})
        histogram.record(duration_ms, attributes)

    def increment_counter(
        self,
        name: str,
        value: int | float = 1,
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        counter = self.create_counter(name)
        counter.add(value, attributes)

    def create_histogram(
        self,
        name: str,
        options: Mapping[str, Any] | None = None,
    ):
        meter = self._root.get_meter()
        if meter is None:
            return create_noop_histogram()
        return meter.create_histogram(name, unit=(options or {}).get("unit"))

    def create_counter(
        self,
        name: str,
        options: Mapping[str, Any] | None = None,
    ):
        meter = self._root.get_meter()
        if meter is None:
            return create_noop_counter()
        return meter.create_counter(name, unit=(options or {}).get("unit"))


class _LogsService:
    def __init__(self, root: _OtelRuntime):
        self._root = root

    def emit(self, record: Mapping[str, Any]) -> None:
        logger = self._root.get_log_emitter()
        if logger is None:
            return
        active_span = self._root.trace.get_active_span()
        span_context = active_span.span_context() if active_span else None
        logger.emit(
            {
                "body": record.get("body"),
                "severity_number": record.get("severity_number"),
                "severity_text": record.get("severity_text"),
                "attributes": record.get("attributes"),
                "trace_id": span_context.get("trace_id") if span_context else None,
                "span_id": span_context.get("span_id") if span_context else None,
            }
        )


class _OtelRuntime:
    def __init__(self, context: CommonContext):
        self.context = context
        self.modules: dict[str, Any] = {}
        self.trace = _TraceService(self)
        self.metrics = _MetricsService(self)
        self.logs = _LogsService(self)

    def setup_otel(self) -> None:
        self._load_modules()

    def run_with_trace_and_metrics(
        self,
        options: Mapping[str, Any],
        fn: Callable[[], Any],
    ) -> Any:
        layer_name = str(options.get("layer_name"))
        domain = str(options.get("domain"))
        function_name = str(options.get("function_name"))
        metric_attrs = layer_metric_attrs(layer_name, domain, function_name)
        span_attrs = span_attributes_from_ids(
            options.get("get_ids", lambda: [])(), layer_name, domain, function_name
        )
        kind = span_kind_for_layer(layer_name)
        span_name = layer_span_name(layer_name, domain, function_name)

        def _run(span: Any) -> Any:
            started_at = time.perf_counter()
            self._record_wrap_span_event(span, options, "start")
            try:
                result = fn()
                self._record_wrap_span_event(span, options, "end", result=result)
                duration_ms = (time.perf_counter() - started_at) * 1000
                self.metrics.record_duration(
                    "layer.function.duration", duration_ms, metric_attrs
                )
                self.metrics.increment_counter("layer.function.calls", 1, metric_attrs)
                self.metrics.increment_counter(
                    "layer.function.success", 1, metric_attrs
                )
                return result
            except Exception as error:
                span.set_status({"code": _SPAN_STATUS_ERROR, "message": str(error)})
                duration_ms = (time.perf_counter() - started_at) * 1000
                self.metrics.record_duration(
                    "layer.function.duration", duration_ms, metric_attrs
                )
                self.metrics.increment_counter("layer.function.calls", 1, metric_attrs)
                self.metrics.increment_counter(
                    "layer.function.errors",
                    1,
                    {**metric_attrs, "error.code": "INTERNAL_ERROR"},
                )
                raise

        return self.trace.run_with_span(
            span_name,
            _run,
            {"attributes": span_attrs, "kind": kind},
        )

    def is_trace_enabled(self) -> bool:
        return self._signal_enabled("trace")

    def is_metrics_enabled(self) -> bool:
        return self._signal_enabled("metrics")

    def is_logs_enabled(self) -> bool:
        return self._signal_enabled("logs")

    def get_tracer(self):
        if not self.is_trace_enabled():
            return None
        self._load_modules()
        trace_api = self.modules.get("trace")
        if not trace_api:
            return None
        get_tracer = getattr(trace_api, "get_tracer", None)
        if callable(get_tracer):
            return get_tracer(self._service_name(), self._version())
        return None

    def get_meter(self):
        if not self.is_metrics_enabled():
            return None
        self._load_modules()
        metrics_api = self.modules.get("metrics")
        if not metrics_api:
            return None
        get_meter = getattr(metrics_api, "get_meter", None)
        if callable(get_meter):
            return get_meter(self._service_name(), self._version())
        provider = getattr(metrics_api, "get_meter_provider", None)
        if callable(provider):
            meter_provider = provider()
            return meter_provider.get_meter(self._service_name(), self._version())
        return None

    def get_log_emitter(self):
        if not self.is_logs_enabled():
            return None
        self._load_modules()
        logs_api = self.modules.get("logs")
        if not logs_api:
            return None
        provider = getattr(logs_api, "get_logger_provider", None)
        if callable(provider):
            return provider().get_logger(self._service_name(), self._version())
        get_logger = getattr(logs_api, "get_logger", None)
        if callable(get_logger):
            return get_logger(self._service_name(), self._version())
        return None

    def _load_modules(self) -> None:
        if self.modules:
            return
        try:
            self.modules["trace"] = importlib.import_module("opentelemetry.trace")
        except ModuleNotFoundError:
            self.modules["trace"] = None
        try:
            self.modules["metrics"] = importlib.import_module("opentelemetry.metrics")
        except ModuleNotFoundError:
            self.modules["metrics"] = None
        try:
            self.modules["logs"] = importlib.import_module("opentelemetry._logs")
        except ModuleNotFoundError:
            self.modules["logs"] = None

    def _signal_enabled(self, key: str) -> bool:
        config = self._otel_config()
        if not config:
            return False
        signal = config.get(key)
        return bool(signal and signal.get("enabled") is True)

    def _otel_config(self) -> Mapping[str, Any]:
        logging_cfg = self.context.config.in_layers_core.logging
        return dict(logging_cfg.get("otel") or {})

    def _service_name(self) -> str:
        return (
            self._otel_config().get("service_name") or self.context.config.system_name
        )

    def _version(self) -> str:
        return self._otel_config().get("version") or "1.0.0"

    def _record_wrap_span_event(
        self,
        span: Any,
        options: Mapping[str, Any],
        phase: str,
        result: Any | None = None,
    ) -> None:
        wrap_options = dict(options.get("wrap_span_events") or {})
        if wrap_options.get("omit_data") is True:
            return
        max_log_chars = self.context.config.in_layers_core.logging.get(
            "max_log_size_in_characters", 50000
        )
        if phase == "start" and "args" in wrap_options:
            span.add_event(
                "nil.execute.start",
                {
                    "args": cap_for_logging(wrap_options.get("args"), max_log_chars),
                },
            )
        if (
            phase == "end"
            and wrap_options.get("record_result") is True
            and result is not None
        ):
            span.add_event(
                "nil.execute.end",
                {
                    "result": cap_for_logging(result, max_log_chars),
                },
            )


def create(context: CommonContext):
    return _OtelRuntime(context)
