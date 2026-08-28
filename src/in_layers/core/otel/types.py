from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from ..protocols import LogId

AttributeValue = str | int | float | bool
AttributesMap = Mapping[str, AttributeValue]


class SpanContextLike(Protocol):
    trace_id: str
    span_id: str


class SpanLike(Protocol):
    def end(self) -> None: ...

    def set_attribute(self, key: str, value: AttributeValue) -> None: ...

    def set_status(self, status: Mapping[str, Any]) -> None: ...

    def add_event(
        self, name: str, attributes: Mapping[str, Any] | None = None
    ) -> None: ...

    def span_context(self) -> SpanContextLike | None: ...


class HistogramLike(Protocol):
    def record(
        self, value: int | float, attributes: AttributesMap | None = None
    ) -> None: ...


class CounterLike(Protocol):
    def add(
        self, value: int | float = 1, attributes: AttributesMap | None = None
    ) -> None: ...


class OtelTraceService(Protocol):
    def start_span(
        self,
        name: str,
        options: Mapping[str, Any] | None = None,
    ) -> SpanLike: ...

    def run_with_span(
        self,
        name: str,
        fn: Callable[[SpanLike], Any],
        options: Mapping[str, Any] | None = None,
    ) -> Any: ...

    def get_active_span(self) -> SpanLike | None: ...


class OtelMetricsService(Protocol):
    def record_duration(
        self,
        name: str,
        duration_ms: int | float,
        attributes: AttributesMap | None = None,
    ) -> None: ...

    def increment_counter(
        self,
        name: str,
        value: int | float = 1,
        attributes: AttributesMap | None = None,
    ) -> None: ...

    def create_histogram(
        self,
        name: str,
        options: Mapping[str, Any] | None = None,
    ) -> HistogramLike: ...

    def create_counter(
        self,
        name: str,
        options: Mapping[str, Any] | None = None,
    ) -> CounterLike: ...


class OtelLogsService(Protocol):
    def emit(self, record: Mapping[str, Any]) -> None: ...


class RunWithTraceAndMetricsOptions(Protocol):
    layer_name: str
    domain: str
    function_name: str
    get_ids: Callable[[], list[LogId]]
    wrap_span_events: Mapping[str, Any] | None


class OtelServices(Protocol):
    def setup_otel(self) -> None: ...

    trace: OtelTraceService
    metrics: OtelMetricsService
    logs: OtelLogsService

    def run_with_trace_and_metrics(
        self,
        options: RunWithTraceAndMetricsOptions | Mapping[str, Any],
        fn: Callable[[], Any],
    ) -> Any: ...
