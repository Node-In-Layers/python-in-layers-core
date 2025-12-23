from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any, Literal, Protocol, Self, TypeAlias

from box import Box
from pydantic import BaseModel
from pydantic.dataclasses import dataclass

# ==============================
# Search/Query Type Definitions
# ==============================


class EqualitySymbol(Enum):
    eq = "="
    lt = "<"
    lte = "<="
    gt = ">"
    gte = ">="
    ne = "!="


AllowableEqualitySymbols: tuple[str, ...] = (
    EqualitySymbol.eq,
    EqualitySymbol.lt,
    EqualitySymbol.lte,
    EqualitySymbol.gt,
    EqualitySymbol.gte,
    EqualitySymbol.ne,
)


class DatastoreValueType(Enum):
    string = "string"
    number = "number"
    date = "date"
    object = "object"
    boolean = "boolean"


class SortOrder(Enum):
    asc = "asc"
    dsc = "dsc"


@dataclass(frozen=True)
class SortStatement:
    key: str
    order: SortOrder


@dataclass(frozen=True)
class PropertyOptions:
    case_sensitive: bool | None = None
    starts_with: bool | None = None
    ends_with: bool | None = None
    includes: bool | None = None
    type: DatastoreValueType | None = None
    equality_symbol: EqualitySymbol | None = None


@dataclass(frozen=True)
class PropertyQuery:
    type: Literal["property"]
    key: str
    value: Any
    value_type: DatastoreValueType
    equality_symbol: EqualitySymbol
    options: PropertyOptions


@dataclass(frozen=True)
class _DatesAfterOptions:
    equal_to_and_after: bool


@dataclass(frozen=True)
class DatesAfterQuery:
    type: Literal["datesAfter"]
    key: str
    date: str
    value_type: DatastoreValueType
    options: _DatesAfterOptions


@dataclass(frozen=True)
class _DatesBeforeOptions:
    equal_to_and_before: bool


@dataclass(frozen=True)
class DatesBeforeQuery:
    type: Literal["datesBefore"]
    key: str
    date: str
    value_type: DatastoreValueType
    options: _DatesBeforeOptions


# Queries supported
type Query = PropertyQuery | DatesAfterQuery | DatesBeforeQuery

AND = Literal["AND"]
OR = Literal["OR"]

# Boolean link tokens
BooleanQuery: TypeAlias = Literal["AND", "OR"]  # noqa: UP040

# Recursive token structure:
# - either a nested list of tokens
# - or a boolean link
# - or a concrete query
type QueryTokens = "list[QueryTokens]" | BooleanQuery | Query


@dataclass(frozen=True)
class ModelSearch:
    """
    A recursive, structured search object used by backends.
    """

    query: list[QueryTokens]
    take: int | None = None
    sort: SortStatement | None = None
    page: Any | None = None


class ModelSearchResult(Protocol):
    instances: list[Mapping]
    page: Any | None


type PrimaryKeyType = str | int


@dataclass(frozen=True)
class ModelDefinition(Protocol):
    domain: str
    plural_name: str
    primary_key: str


class _ZeroArgGetter(Protocol):
    def __call__(self) -> Any: ...


class _GetterAccessor(Protocol):
    """
    Dynamic accessor that exposes zero-arg getters for instance properties.
    Example: instance.get.id() -> Any
    """

    def __getattr__(self, name: str) -> _ZeroArgGetter: ...


class InLayersModelInstance(Protocol):
    """
    An instance of a simple model. Wraps the data itself and provides methods to work with it.
    """

    #: Gets the underlying model
    def get_model(self) -> InLayersModel: ...

    #: Updates the instance with the provided data
    def update(self, **kwargs) -> Self: ...

    #: Deletes the instance from the backend
    def delete(self) -> None: ...

    #: Converts the instance data to a Box dictionary
    def to_dict(self) -> Box: ...

    #: Converts the instance data to a Pydantic model
    def to_pydantic(self) -> BaseModel: ...

    #: Validates the instance data against the model schema
    def validate(self) -> None: ...

    #: Gets the primary key value of the instance
    def get_primary_key(self) -> PrimaryKeyType: ...

    @property
    def get(self) -> _GetterAccessor: ...


class InLayersModel(Protocol):
    """
    A Simple Model that describes (abstractly) data that is stored in a backend.
    Also provides CRUDS interface for working with the data.
    """

    #: Gets metadata about the model
    def get_model_definition(self) -> ModelDefinition: ...

    #: Creates a non-persisted instance wrapper from mapping or keyword args
    def instance(
        self, data: Mapping | None = None, **kwargs: Any
    ) -> InLayersModelInstance: ...

    #: Creates a new instance of the model with the provided data
    def create(
        self, data: Mapping | None = None, **kwargs: Any
    ) -> InLayersModelInstance: ...

    #: Retrieves an instance of the model by its primary key
    def retrieve(self, id: PrimaryKeyType) -> InLayersModelInstance | None: ...

    #: Validates the provided data against the model schema
    def validate(self, data: Mapping) -> None: ...

    #: Updates an existing instance of the model by its primary key with the provided data
    def update(self, id: PrimaryKeyType, **kwargs: Any) -> InLayersModelInstance: ...

    #: Deletes an instance of the model by its primary key
    def delete(self, id: PrimaryKeyType) -> None: ...

    #: Searches for instances of the model matching the query
    def search(self, query: ModelSearch) -> ModelSearchResult: ...

    #: Bulk inserts a list of instances of the model with the provided data
    def bulk_insert(self, data: list[Mapping]) -> None: ...

    #: Bulk deletes a list of instances of the model by their primary keys
    def bulk_delete(self, ids: list[PrimaryKeyType]) -> None: ...

    #: Gets the name of the primary key field
    def get_primary_key_name(self) -> str: ...

    #: Gets the primary key value from the provided model data
    def get_primary_key(self, model_data: Mapping) -> PrimaryKeyType: ...

    #: Converts the model data to a Pydantic model
    def to_pydantic(self, data: Mapping) -> BaseModel: ...


class BackendProtocol(Protocol):

    def create(self, model: InLayersModel, data: Mapping) -> Mapping: ...
    def retrieve(self, model: InLayersModel, id: PrimaryKeyType) -> Mapping | None: ...
    def update(
        self, model: InLayersModel, id: PrimaryKeyType, data: Mapping
    ) -> Mapping: ...
    def delete(self, model: InLayersModel, id: PrimaryKeyType) -> None: ...
    def search(self, model: InLayersModel, query: ModelSearch) -> ModelSearchResult: ...
    def bulk_insert(self, model: InLayersModel, data: list[Mapping]) -> None: ...
    def bulk_delete(self, model: InLayersModel, ids: list[PrimaryKeyType]) -> None: ...
    def dispose(self) -> None: ...


ModelServices = Mapping[str, InLayersModel]
