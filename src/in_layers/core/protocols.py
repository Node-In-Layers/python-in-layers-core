from __future__ import annotations

import datetime
from collections.abc import Awaitable, Callable, Mapping
from enum import Enum
from typing import (
    Any,
    NotRequired,
    Protocol,
    TypedDict,
)

# --- Enums ---


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


# --- Core Data Types ---

LogId = Mapping[str, str]


class ErrorDetails(TypedDict, total=False):
    code: str
    message: str
    details: str
    data: dict[str, Any]
    trace: str
    cause: ErrorDetails


class ErrorObject(TypedDict):
    error: ErrorDetails


class LogMessage(TypedDict, total=False):
    id: str
    logger: str
    environment: str
    ids: list[LogId]
    log_level: LogLevelNames
    datetime: datetime.datetime
    message: str
    # extra fields allowed


LogFunction = Callable[[LogMessage], Any]
LogMethod = Callable[["CommonContext"], LogFunction]


class CrossLayerLogging(TypedDict, total=False):
    ids: list[LogId]


class CrossLayerProps(TypedDict, total=False):
    logging: CrossLayerLogging


# --- Config Types ---


class CoreLoggingConfig(TypedDict, total=False):
    log_level: LogLevelNames
    log_format: LogFormat | list[LogFormat]
    max_log_size_in_characters: int
    tcp_logging_options: Mapping[str, Any]
    custom_logger: RootLogger
    get_function_wrap_log_level: Callable[[str, str | None], LogLevelNames]
    ignore_layer_functions: Mapping[str, bool | Mapping[str, bool | Mapping[str, bool]]]


class App(TypedDict, total=False):
    name: str
    description: str
    services: Mapping[str, Any]
    features: Mapping[str, Any]
    globals: Mapping[str, Any]
    models: Mapping[str, ModelConstructor]


LayerDescription = str | list[str]


class CoreConfig(TypedDict, total=False):
    logging: CoreLoggingConfig
    layer_order: list[LayerDescription]
    # Python canonical: domains; Back-compat alias: apps
    domains: list[App]
    apps: list[App]
    model_factory: str
    model_cruds: bool
    custom_model_factory: Mapping[str, Any]


class CommonConstants(TypedDict):
    environment: str
    working_directory: str
    runtime_id: str


class Config(Protocol):
    system_name: str
    environment: str

    def __getitem__(self, key: str) -> Any: ...


class RootLogger(Protocol):
    def get_logger(
        self,
        context: CommonContext,
        props: Mapping[str, Any] | None = None,
    ) -> HighLevelLogger: ...


# --- Logger Protocols ---


class Logger(Protocol):
    def trace(
        self,
        message: str,
        data_or_error: Mapping[str, Any] | None = None,
        *,
        ignore_size_limit: bool = False,
    ) -> Any: ...

    def debug(
        self,
        message: str,
        data_or_error: Mapping[str, Any] | None = None,
        *,
        ignore_size_limit: bool = False,
    ) -> Any: ...

    def info(
        self,
        message: str,
        data_or_error: Mapping[str, Any] | None = None,
        *,
        ignore_size_limit: bool = False,
    ) -> Any: ...

    def warn(
        self,
        message: str,
        data_or_error: Mapping[str, Any] | None = None,
        *,
        ignore_size_limit: bool = False,
    ) -> Any: ...

    def error(
        self,
        message: str,
        data_or_error: Mapping[str, Any] | None = None,
        *,
        ignore_size_limit: bool = False,
    ) -> Any: ...

    def apply_data(self, data: Mapping[str, Any]) -> Logger: ...

    def get_id_logger(
        self, name: str, log_id_or_key: LogId | str, id: str | None = None
    ) -> Logger: ...

    def get_sub_logger(self, name: str) -> Logger: ...

    def get_ids(self) -> list[LogId]: ...


FunctionLogger = Logger


class LayerLogger(Logger, Protocol):
    def _log_wrap(
        self, function_name: str, func: Callable[..., Any]
    ) -> Callable[..., Any]: ...

    def _log_wrap_async(
        self, function_name: str, func: Callable[..., Any]
    ) -> Callable[..., Any]: ...

    def _log_wrap_sync(
        self, function_name: str, func: Callable[..., Any]
    ) -> Callable[..., Any]: ...

    def get_function_logger(
        self, name: str, cross_layer_props: CrossLayerProps | None = None
    ) -> FunctionLogger: ...

    def get_inner_logger(
        self, function_name: str, cross_layer_props: CrossLayerProps | None = None
    ) -> FunctionLogger: ...


class AppLogger(Logger, Protocol):
    def get_layer_logger(
        self,
        layer_name: CommonLayerName | str,
        cross_layer_props: CrossLayerProps | None = None,
    ) -> LayerLogger: ...


class HighLevelLogger(Logger, Protocol):
    def get_app_logger(self, app_name: str) -> AppLogger: ...


# --- Contexts & Layer Contracts ---


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


class AppLayer(Protocol):
    def create(self, context: LayerContext) -> Any: ...


class ServicesLayerFactory(Protocol):
    def create(self, context: ServicesContext) -> Any: ...


class FeaturesLayerFactory(Protocol):
    def create(self, context: FeaturesContext) -> Any: ...


# --- Models (shape only; implementations elsewhere) ---


class ModelConstructor(Protocol):
    def create(self, model_props: Mapping[str, Any]) -> Any: ...


# --- Helper marker (typing hint only) ---


def layer_function(fn: Callable[..., Any]) -> Callable[..., Any]:
    return fn


# --- Globals typed shapes (for create functions) ---


class GlobalsServicesProps(TypedDict):
    environment: str
    working_directory: str
    runtime_id: NotRequired[str]


class GlobalsServices(TypedDict):
    load_config: Callable[[], Config]
    get_root_logger: Callable[[], RootLogger]
    get_constants: Callable[[], CommonConstants]
    get_globals: Callable[[CommonContext, App], Awaitable[Mapping[str, Any]]]


class GlobalsFeatures(TypedDict):
    load_globals: Callable[[str | Config], Awaitable[CommonContext]]


# --- Layers typed shapes (for create functions) ---


class LayersServices(TypedDict):
    get_model_props: Callable[[ServicesContext], Mapping[str, Any]]
    load_layer: Callable[[App, str, LayerContext], Mapping[str, Any] | None]


class LayersFeatures(TypedDict):
    load_layers: Callable[[], Awaitable[FeaturesContext]]
