from __future__ import annotations

import datetime
from collections.abc import Awaitable, Callable, Mapping
from enum import Enum
from typing import (
    Any,
    Protocol,
    TypeVar,
)

from pydantic import Field
from pydantic.dataclasses import dataclass
from pydantic_core import core_schema

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
    root = "in_layers_core"
    globals = "in_layers_core_globals"
    layers = "in_layers_core_layers"
    models = "in_layers_core_models"


class CommonLayerName(str, Enum):
    models = "models"
    services = "services"
    features = "features"
    entries = "entries"


# ======================================================================
# Generic helpers / aliases
# ======================================================================

JsonAble = None | bool | int | float | str | Mapping[str, Any] | list[Any]

MaybeAwaitable = TypeVar("MaybeAwaitable", bound=Any | Awaitable[Any])
LogId = Mapping[str, str]


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


class CrossLayerLogging(Protocol):
    """
    Properties useful for logging and tracing across layers.
    """

    # model_config = ConfigDict(extra="allow")
    ids: list[LogId] = Field(
        ..., description="List of log ids to be used for tracing across layers."
    )


class CrossLayerProps(Protocol):
    """
    Properties that are useful across layers. Useful for passing along logging and tracing information across layers.
    """

    # model_config = ConfigDict(extra="allow")
    logging: CrossLayerLogging | None = Field(
        ..., description="Properties useful for logging and tracing across layers."
    )


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
        self, context: CommonContext, props: Mapping[str, Any] | None
    ) -> HighLevelLogger: ...


# ======================================================================
# Logging configuration
# ======================================================================


class CoreLoggingConfig(Protocol):
    log_level: LogLevelNames
    log_format: LogFormat | list[LogFormat]
    max_log_size_in_characters: int | None
    tcp_logging_options: Mapping[str, Any] | None
    custom_logger: Any | None
    # domain, domain.layer, domain.layer.functionName
    ignore_layer_functions: list[str]
    # (layerName, functionName?) -> logLevel
    get_function_wrap_log_level: Callable[[str, str | None], LogLevelNames] | None


# ======================================================================
# Config / context
# ======================================================================


@dataclass(frozen=True)
class CommonConstants:
    environment: str
    working_directory: str
    runtime_id: str


# Forward refs
class Domain(Protocol):
    description: str | None = Field(None, description="The description of the domain.")
    services: Any | None = Field(None, description="The services layer for the domain.")
    features: Any | None = Field(None, description="The features layer for the domain.")
    globals: Any | None = Field(None, description="The globals layer for the domain.")

    @classmethod
    def __get_pydantic_core_schema__(cls, source, handler):
        # Tell Pydantic to treat AppLayer as an opaque type
        return core_schema.any_schema()


LayerDescription = str | list[str]


class CoreConfig(Protocol):
    logging: CoreLoggingConfig
    layer_order: list[LayerDescription]
    domains: list[Domain]
    model_factory: str | None
    model_cruds: bool
    custom_model_factory: Mapping[str, Any] | None


class Config(Protocol):
    system_name: str
    environment: str
    in_layers_core: CoreConfig


class CommonContext(Protocol):
    config: Config
    root_logger: Any
    constants: CommonConstants


class LayerContext(CommonContext):
    log: LayerLogger


class ServicesContext(LayerContext):
    services: Mapping[str, Any]


class FeaturesContext(LayerContext):
    services: Mapping[str, Any]
    features: Mapping[str, Any]


class AppLayer(Protocol):
    def create(self, context: CommonContext) -> Awaitable[Mapping[str, Any]]: ...

    def __get_pydantic_core_schema__(self, source, handler):
        # Tell Pydantic to treat AppLayer as an opaque type
        return core_schema.any_schema()


class GlobalsLayer(Protocol):
    def create(self, context: CommonContext) -> Awaitable[Mapping[str, Any]]: ...

    @classmethod
    def __get_pydantic_core_schema__(cls, source, handler):
        # Tell Pydantic to treat AppLayer as an opaque type
        return core_schema.any_schema()


@dataclass(frozen=True)
class GlobalsServicesProps:
    environment: str
    working_directory: str
    runtime_id: str | None = None
