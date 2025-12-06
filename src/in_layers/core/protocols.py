from __future__ import annotations

import datetime
from collections.abc import Awaitable, Callable, Mapping
from enum import Enum
from typing import (
    Any,
    Protocol,
    TypedDict,
    TypeVar,
    Union,
)

from pydantic import BaseModel, ConfigDict, Field
from pydantic.dataclasses import dataclass

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
    root = "in-layers/core"
    globals = "in-layers/core/globals"
    layers = "in-layers/core/layers"
    models = "in-layers/core/models"


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
    code: str = Field(..., description="A unique string code for the error")
    message: str = Field(..., description="A user friendly error message.")
    details: str | None = Field(
        None, description="Additional details in a string format."
    )
    data: Mapping[str, JsonAble] | None = Field(
        None, description="Additional data as an object."
    )
    trace: str | None = Field(None, description="A trace of the error.")
    cause: ErrorObject | None = Field(
        None, description="A suberror that has the cause of the error."
    )


@dataclass(frozen=True)
class ErrorObject:
    error: ErrorDetails = Field(..., description="The error details.")


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


class CrossLayerLogging(BaseModel):
    model_config = ConfigDict(extra="allow")
    ids: list[LogId]


class CrossLayerProps(BaseModel):
    model_config = ConfigDict(extra="allow")
    logging: CrossLayerLogging | None
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
    log_level: LogLevelNames = Field(..., description="The default log level.")
    log_format: LogFormat | list[LogFormat] = Field(
        ..., description="The log format or formats to use."
    )
    max_log_size_in_characters: int | None = Field(
        None, description="The maximum log size in characters."
    )
    tcp_logging_options: Mapping[str, Any] | None = Field(
        None, description="The TCP logging options."
    )
    custom_logger: Any | None = Field(None, description="The custom logger to use.")

    # domain -> (bool | (layer -> (bool | (function -> bool))))
    ignore_layer_functions: (
        Mapping[str, bool | Mapping[str, bool | Mapping[str, bool]]] | None
    ) = Field(None, description="The functions to ignore.")
    # (layerName, functionName?) -> logLevel
    get_function_wrap_log_level: Callable[[str, str | None], LogLevelNames] | None = (
        Field(None, description="The function to get the log level for a function.")
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


LayerDescription = str | list[str]


@dataclass(frozen=True)
class CoreConfig:
    logging: CoreLoggingConfig = Field(..., description="The logging configuration.")
    layer_order: list[LayerDescription] = Field(
        ..., description="The order of the layers to load."
    )
    domains: list[App] = Field(..., description="The domains/apps to load.")
    model_factory: str | None = Field(
        None,
        description="The namespace to the domain.services that has a 'getModelProps()' function used for loading models.",
    )
    model_cruds: bool = Field(
        False,
        description="When true, wrappers are built around models to bubble up CRUDS interfaces for models through services and features.",
    )
    custom_model_factory: Mapping[str, Any] | None = Field(
        None, description="Provides granular 'getModelProps()' for specific models."
    )


class Config(BaseModel):
    model_config = ConfigDict(extra="allow")
    system_name: str = Field(..., description="The name of the system.")
    environment: str = Field(
        ..., description="The environment the system is running in."
    )
    in_layers_core: CoreConfig = Field(..., description="The core configuration.")


class CommonContext(BaseModel):
    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)
    config: Config = Field(..., description="The configuration for the system.")
    root_logger: Any = Field(..., description="The root logger for the system.")
    constants: CommonConstants = Field(..., description="The constants for the system.")


class LayerContext(CommonContext):
    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)
    log: Any


class ServicesContext(LayerContext):
    models: Mapping[str, Mapping[str, Callable[[], Any]]]
    services: Mapping[str, Any]


class FeaturesContext(LayerContext):
    services: Mapping[str, Any]
    features: Mapping[str, Any]


# ======================================================================
# Core "App" (domain) shape
# ======================================================================


class Domain(BaseModel):
    name: str = Field(..., description="The name of the domain.")
    description: str | None = Field(None, description="The description of the domain.")
    services: AppLayer | None = Field(
        None, description="The services layer for the domain."
    )
    features: AppLayer | None = Field(
        None, description="The features layer for the domain."
    )
    globals: GlobalsLayer | None = Field(
        None, description="The globals layer for the domain."
    )


class AppLayer(Protocol):
    def create(self, context: CommonContext) -> Awaitable[Mapping[str, Any]]: ...


class GlobalsLayer(Protocol):
    def create(self, context: CommonContext) -> Awaitable[Mapping[str, Any]]: ...


class GlobalsServicesProps(TypedDict, total=False):
    environment: str
    working_directory: str
