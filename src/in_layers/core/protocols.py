from __future__ import annotations

import datetime
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import (
    Any,
    NotRequired,
    Protocol,
    TypedDict,
    TypeVar,
    Union,
)

# ======================================================================
# Core enums
# ======================================================================


class LogLevel(Enum):
    TRACE = 0
    DEBUG = 1
    INFO = 2
    WARN = 3
    ERROR = 4
    SILENT = 5


class LogLevelNames(str, Enum):
    trace = "trace"
    debug = "debug"
    info = "info"
    warn = "warn"
    error = "error"
    silent = "silent"


class LogFormat(str, Enum):
    json = "json"
    custom = "custom"
    simple = "simple"
    tcp = "tcp"
    full = "full"


class CoreNamespace(str, Enum):
    root = "@node-in-layers/core"
    globals = "@node-in-layers/core/globals"
    layers = "@node-in-layers/core/layers"
    models = "@node-in-layers/core/models"


class CommonLayerName(str, Enum):
    models = "models"
    services = "services"
    features = "features"
    entries = "entries"


# ======================================================================
# Generic helpers / aliases
# ======================================================================

JsonAble = Union[
    None,
    bool,
    int,
    float,
    str,
    Mapping[str, Any],
    list[Any],
]

LogId = Mapping[str, str]
MaybeAwaitable = TypeVar("MaybeAwaitable", bound=Any | Awaitable[Any])


# ======================================================================
# Error / logging base shapes
# ======================================================================


@dataclass(frozen=True)
class ErrorDetails:
    code: str
    message: str
    details: str | None = None
    data: Mapping[str, JsonAble] | None = None
    trace: str | None = None
    cause: ErrorObject | None = None


@dataclass(frozen=True)
class ErrorObject:
    error: ErrorDetails


@dataclass(frozen=True)
class LogInstanceOptions:
    ignore_size_limit: bool | None = None


@dataclass(frozen=True)
class LogMessage:
    id: str
    logger: str
    environment: str
    log_level: LogLevelNames
    datetime: datetime.datetime
    message: str
    ids: list[LogId] | None = None
    # arbitrary extra fields are allowed by convention; not modeled here


LogFunction = Callable[[LogMessage], Any]


class LogMethod(Protocol):
    def __call__(self, context: CommonContext) -> LogFunction: ...


# ======================================================================
# Cross-layer props
# ======================================================================


class CrossLayerLogging(TypedDict, total=False):
    ids: list[LogId]


class CrossLayerProps(TypedDict, total=False):
    logging: NotRequired[CrossLayerLogging]
    # additional ad‑hoc fields are possible but not explicitly typed


# ======================================================================
# Logger protocols
# ======================================================================


class Logger(Protocol):
    def trace(
        self,
        message: str,
        data_or_error: Mapping[str, JsonAble | object] | ErrorObject | None = None,
        options: LogInstanceOptions | None = None,
    ) -> Any: ...

    def debug(
        self,
        message: str,
        data_or_error: Mapping[str, JsonAble | object] | ErrorObject | None = None,
        options: LogInstanceOptions | None = None,
    ) -> Any: ...

    def info(
        self,
        message: str,
        data_or_error: Mapping[str, JsonAble | object] | ErrorObject | None = None,
        options: LogInstanceOptions | None = None,
    ) -> Any: ...

    def warn(
        self,
        message: str,
        data_or_error: Mapping[str, JsonAble | object] | ErrorObject | None = None,
        options: LogInstanceOptions | None = None,
    ) -> Any: ...

    def error(
        self,
        message: str,
        data_or_error: Mapping[str, JsonAble | object] | ErrorObject | None = None,
        options: LogInstanceOptions | None = None,
    ) -> Any: ...

    def apply_data(self, data: Mapping[str, JsonAble]) -> Logger: ...

    def get_id_logger(
        self,
        name: str,
        log_id_or_key: LogId | str,
        id: str | None = None,
    ) -> Logger: ...

    def get_sub_logger(self, name: str) -> Logger: ...

    def get_ids(self) -> list[LogId]: ...


FunctionLogger = Logger


class LayerLogger(Logger, Protocol):
    def _log_wrap(
        self,
        function_name: str,
        func: Callable[..., Any],
    ) -> Callable[..., Any]: ...

    def _log_wrap_async(
        self,
        function_name: str,
        func: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]: ...

    def _log_wrap_sync(
        self,
        function_name: str,
        func: Callable[..., Any],
    ) -> Callable[..., Any]: ...

    def get_function_logger(
        self,
        name: str,
        cross_layer_props: CrossLayerProps | None = None,
    ) -> FunctionLogger: ...

    def get_inner_logger(
        self,
        function_name: str,
        cross_layer_props: CrossLayerProps | None = None,
    ) -> FunctionLogger: ...


class AppLogger(Logger, Protocol):
    def get_layer_logger(
        self,
        layer_name: CommonLayerName | str,
        cross_layer_props: CrossLayerProps | None = None,
    ) -> LayerLogger: ...


class HighLevelLogger(Logger, Protocol):
    def get_app_logger(self, app_name: str) -> AppLogger: ...


class RootLogger(Protocol):
    def get_logger(
        self,
        context: CommonContext,
        props: Mapping[str, Any] | None = None,
    ) -> HighLevelLogger: ...


# ======================================================================
# Logging configuration
# ======================================================================


@dataclass(frozen=True)
class CoreLoggingConfig:
    log_level: LogLevelNames
    log_format: LogFormat | list[LogFormat]
    max_log_size_in_characters: int | None = None
    tcp_logging_options: Mapping[str, Any] | None = None
    custom_logger: RootLogger | None = None

    # domain -> (bool | (layer -> (bool | (function -> bool))))
    ignore_layer_functions: (
        Mapping[
            str,
            bool | Mapping[str, bool | Mapping[str, bool]],
        ]
        | None
    ) = None

    # (layerName, functionName?) -> logLevel
    get_function_wrap_log_level: Callable[[str, str | None], LogLevelNames] | None = (
        None
    )


# ======================================================================
# Config / context
# ======================================================================


class CommonConstants(TypedDict):
    environment: str
    working_directory: str
    runtime_id: str


# Forward refs
class App(TypedDict):  # defined fully below
    ...


LayerDescription = Union[str, list[str]]


@dataclass(frozen=True)
class CoreConfig:
    logging: CoreLoggingConfig
    layer_order: list[LayerDescription]
    apps: list[App]  # like TS `apps: readonly App[]`
    model_factory: str | None = None
    model_cruds: bool = False
    custom_model_factory: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class Config:
    system_name: str
    environment: str
    core: CoreConfig  # equivalent to TS `[CoreNamespace.root]: CoreConfig`


class CommonContext(TypedDict):
    config: Config
    root_logger: RootLogger
    constants: CommonConstants


class LayerContext(CommonContext, total=False):
    log: LayerLogger


class ServicesContext(LayerContext, total=False):
    models: Mapping[str, Mapping[str, Callable[[], Any]]]
    services: Mapping[str, Any]


class FeaturesContext(LayerContext, total=False):
    services: Mapping[str, Any]
    features: Mapping[str, Any]


# ======================================================================
# Models-related (only the parts you mirror / need)
# ======================================================================

# Placeholder protocol for model constructors/factories, since you said
# "ignore any TS types that aren't represented"; this stays minimal.


class ModelConstructor(Protocol):
    def create(self, *args: Any, **kwargs: Any) -> Any: ...


# ======================================================================
# Core "App" (domain) shape
# ======================================================================


class App(TypedDict, total=False):
    name: str
    description: NotRequired[str]

    # config-driven layers (services/features/globals/models)
    # Each is a "layer factory": a module-like object with `create(context)`.

    # A generic layer: `create(LayerContext) -> layer instance`
    services: AppLayer
    features: AppLayer
    globals: GlobalsLayer
    models: Mapping[str, ModelConstructor]


# A generic layer factory: create(context) -> layer object
class AppLayer(Protocol):
    def create(self, context: LayerContext) -> Any | Awaitable[Any]: ...


class GlobalsLayer(Protocol):
    def create(self, context: CommonContext) -> Awaitable[Mapping[str, Any]]: ...


# ======================================================================
# Globals shapes
# ======================================================================


class GlobalsServicesProps(TypedDict, total=False):
    environment: str
    working_directory: str
