from __future__ import annotations

from contextlib import contextmanager

from box import Box

from in_layers.core.otel.libs import create_otel_log_method
from in_layers.core.otel.services import create as create_otel_services
from in_layers.core.protocols import CoreNamespace, LogLevelNames


class _FakeSpanContext:
    def __init__(self, trace_id: int, span_id: int):
        self.trace_id = trace_id
        self.span_id = span_id


class _FakeSpan:
    def __init__(self):
        self.attributes: dict[str, object] = {}
        self.events: list[tuple[str, dict[str, object] | None]] = []
        self.status: dict[str, object] | None = None
        self.ended = False
        self.context = _FakeSpanContext(1234, 5678)

    def end(self) -> None:
        self.ended = True

    def add_event(self, name: str, attributes=None) -> None:
        self.events.append((name, attributes))

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def set_status(self, status) -> None:
        self.status = dict(status)

    def get_span_context(self):
        return self.context


class _FakeTraceModule:
    def __init__(self):
        self.current_span: _FakeSpan | None = None
        self.spans: list[_FakeSpan] = []

    def get_tracer(self, _service_name: str, _version: str):
        module = self

        class _Tracer:
            def start_span(
                self, _name: str, attributes=None, kind=None
            ):  # noqa: ARG002
                span = _FakeSpan()
                if attributes:
                    for key, value in attributes.items():
                        span.set_attribute(key, value)
                module.spans.append(span)
                return span

            @contextmanager
            def start_as_current_span(
                self, _name: str, attributes=None, kind=None
            ):  # noqa: ARG002
                span = self.start_span(_name, attributes=attributes, kind=kind)
                previous = module.current_span
                module.current_span = span
                try:
                    yield span
                finally:
                    module.current_span = previous
                    span.end()

        return _Tracer()

    def get_current_span(self):
        return self.current_span or _FakeSpan()


class _FakeHistogram:
    def __init__(self):
        self.records: list[tuple[float, object]] = []

    def record(self, value, attributes=None):
        self.records.append((value, attributes))


class _FakeCounter:
    def __init__(self):
        self.records: list[tuple[float, object]] = []

    def add(self, value=1, attributes=None):
        self.records.append((value, attributes))


class _FakeMeter:
    def __init__(self):
        self.histograms: dict[str, _FakeHistogram] = {}
        self.counters: dict[str, _FakeCounter] = {}

    def create_histogram(self, name: str, unit=None):  # noqa: ARG002
        self.histograms.setdefault(name, _FakeHistogram())
        return self.histograms[name]

    def create_counter(self, name: str, unit=None):  # noqa: ARG002
        self.counters.setdefault(name, _FakeCounter())
        return self.counters[name]


class _FakeMetricsModule:
    def __init__(self):
        self.meter = _FakeMeter()

    def get_meter(self, _service_name: str, _version: str):
        return self.meter


class _FakeOtelLogger:
    def __init__(self):
        self.records: list[dict[str, object]] = []

    def emit(self, record):
        self.records.append(dict(record))


class _FakeLogsModule:
    def __init__(self):
        self.logger = _FakeOtelLogger()

    def get_logger_provider(self):
        module = self

        class _Provider:
            def get_logger(self, _service_name: str, _version: str):
                return module.logger

        return _Provider()


def _ctx():
    return Box(
        {
            "config": {
                CoreNamespace.root.value: {
                    "logging": {
                        "log_level": LogLevelNames.info,
                        "log_format": ["json", "otel"],
                        "max_log_size_in_characters": 1000,
                        "otel": {
                            "service_name": "test-system",
                            "version": "1.2.3",
                            "trace": {"enabled": True},
                            "logs": {"enabled": True},
                            "metrics": {"enabled": True},
                        },
                    },
                    "domains": [],
                    "layer_order": ["services", "features"],
                },
                "system_name": "test-system",
                "environment": "test",
            },
            "constants": {
                "environment": "test",
                "runtime_id": "rid-1",
                "working_directory": "/tmp",
            },
        },
        default_box=True,
        default_box_attr=None,
    )


def test_create_otel_log_method_emits_flattened_attributes():
    collected: list[dict[str, object]] = []
    context = _ctx()
    context["services"] = {
        CoreNamespace.otel.value: {
            "logs": {
                "emit": lambda record: collected.append(record),
            }
        }
    }

    log_message = {
        "id": "msg-1",
        "logger": "demo:features:ping",
        "environment": "test",
        "log_level": LogLevelNames.info,
        "datetime": "2026-01-01T00:00:00Z",
        "message": "hello",
        "ids": [{"runtime_id": "rid-1"}, {"request_id": "req-1"}],
        "args": [{"x": 1}],
    }

    create_otel_log_method()(context)(log_message)

    assert collected[0]["body"] == "hello"
    assert collected[0]["attributes"]["id_runtime_id"] == "rid-1"
    assert collected[0]["attributes"]["id_request_id"] == "req-1"


def test_otel_runtime_run_with_trace_and_metrics_records_events(monkeypatch):
    trace_module = _FakeTraceModule()
    metrics_module = _FakeMetricsModule()
    logs_module = _FakeLogsModule()

    def fake_import_module(name: str):
        if name == "opentelemetry.trace":
            return trace_module
        if name == "opentelemetry.metrics":
            return metrics_module
        if name == "opentelemetry._logs":
            return logs_module
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(
        "in_layers.core.otel.services.importlib.import_module",
        fake_import_module,
    )

    runtime = create_otel_services(_ctx())
    runtime.setup_otel()

    actual = runtime.run_with_trace_and_metrics(
        {
            "layer_name": "features",
            "domain": "demo",
            "function_name": "ping",
            "get_ids": lambda: [{"runtime_id": "rid-1"}],
            "wrap_span_events": {
                "args": [{"x": 1}],
                "record_result": True,
            },
        },
        lambda: {"ok": True},
    )

    expected = {"ok": True}

    assert actual == expected
    assert trace_module.spans
    assert trace_module.spans[0].ended is True
    assert trace_module.spans[0].events[0][0] == "nil.execute.start"
    assert trace_module.spans[0].events[1][0] == "nil.execute.end"
    assert metrics_module.meter.histograms["layer.function.duration"].records
    assert metrics_module.meter.counters["layer.function.calls"].records
    assert metrics_module.meter.counters["layer.function.success"].records


def test_otel_runtime_logs_emit_include_trace_and_span_ids(monkeypatch):
    trace_module = _FakeTraceModule()
    metrics_module = _FakeMetricsModule()
    logs_module = _FakeLogsModule()

    def fake_import_module(name: str):
        if name == "opentelemetry.trace":
            return trace_module
        if name == "opentelemetry.metrics":
            return metrics_module
        if name == "opentelemetry._logs":
            return logs_module
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(
        "in_layers.core.otel.services.importlib.import_module",
        fake_import_module,
    )

    runtime = create_otel_services(_ctx())
    runtime.setup_otel()

    runtime.trace.run_with_span(
        "custom-span",
        lambda _span: runtime.logs.emit(
            {
                "body": "business event",
                "severity_number": 9,
                "severity_text": "info",
                "attributes": {"component": "demo"},
            }
        ),
        {"attributes": {"feature": "demo"}},
    )

    record = logs_module.logger.records[0]
    assert record["body"] == "business event"
    assert record["trace_id"] == format(1234, "032x")
    assert record["span_id"] == format(5678, "016x")
