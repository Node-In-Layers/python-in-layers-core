from collections.abc import Callable, Mapping
from typing import Any, Self

from box import Box
from pydantic import BaseModel

from .backends import MemoryBackend
from .libs import get_model_definition
from .protocols import (
    BackendProtocol,
    InLayersModel,
    InLayersModelInstance,
    ModelDefinition,
    ModelSearchResult,
    PrimaryKeyType,
    ToPydanticOptions,
)


class _InLayersModelInstanceImpl(InLayersModelInstance):

    def __init__(self, model: InLayersModel, data: Mapping):
        self.__model = model
        self.__data = data

    class _GetterAccessor:
        def __init__(self, data: Mapping[str, Any]):
            self.__data = data

        def __getattr__(self, name: str):
            def _getter():
                try:
                    return self.__data[name]
                except Exception:
                    return None

            return _getter

        def __dir__(self):
            try:
                return list(set(object.__dir__(self) + list(self.__data.keys())))
            except Exception:
                return object.__dir__(self)

    def get_model(self) -> InLayersModel:
        return self.__model

    def to_dict(self) -> Box:
        return Box(self.__data)

    def to_pydantic(self, options: ToPydanticOptions | None = None) -> BaseModel:
        return self.__model.to_pydantic(self.__data, options)

    def validate(self) -> None:
        self.__model.validate(self.__data)

    def delete(self) -> None:
        primary_key = self.get_primary_key()
        self.__model.delete(primary_key)

    def update(self, **kwargs) -> Self:
        primary_key = self.get_primary_key()
        return self.__model.update(primary_key, **kwargs)

    def get_primary_key(self) -> PrimaryKeyType:
        return self.__model.get_primary_key(self.__data)

    @property
    def get(self):
        """
        Provides zero-arg getters for each property in the instance data:
            instance.get.id()
            instance.get.name()
        """
        # Expose a dynamic accessor bound to the current data mapping
        return _InLayersModelInstanceImpl._GetterAccessor(self.__data)  # type: ignore[arg-type]


class _InLayersModelImpl(InLayersModel):

    def __init__(self, pydantic_model: BaseModel, backend: BackendProtocol):
        if get_model_definition(pydantic_model) is None:
            raise ValueError("Model is not decorated with @model")
        self.__model = pydantic_model
        self.__backend = backend

    def instance(
        self, data: Mapping | None = None, **kwargs: Any
    ) -> InLayersModelInstance:
        """
        Wraps the data to give model capabilities.
        This does NOT save the data.

        :param self: Description
        :param data: Optional mapping to initialize
        :type data: Mapping | None
        """
        base: dict[str, Any] = {}
        if isinstance(data, Mapping):
            base.update(dict(data))
        if kwargs:
            base.update(kwargs)
        return _InLayersModelInstanceImpl(self, base)

    def get_model_definition(self) -> ModelDefinition:
        return get_model_definition(self.__model)

    def validate(self, data: Mapping) -> None:
        self.__model.validate(data)

    def create(
        self, data: Mapping | None = None, **kwargs: Any
    ) -> InLayersModelInstance:
        payload: dict[str, Any] = {}
        if isinstance(data, Mapping):
            payload.update(dict(data))
        if kwargs:
            payload.update(kwargs)
        created_data = self.__backend.create(self, payload)
        return self.instance(created_data)

    def retrieve(self, id) -> InLayersModelInstance | None:
        data = self.__backend.retrieve(self, id)
        if data is None:
            return None
        return self.instance(data)

    def update(self, id, **kwargs) -> InLayersModelInstance:
        new_data = self.__backend.update(self, id, kwargs)
        return self.instance(new_data)

    def delete(self, id) -> None:
        self.__backend.delete(self, id)

    def search(self, query) -> ModelSearchResult:
        result = self.__backend.search(self, query)
        final_container = Box(
            instances=[self.instance(item) for item in result.instances],
            page=result.page,
        )
        return final_container

    def count(self) -> int:
        return self.__backend.count(self)

    def bulk_insert(self, data: list[Mapping]) -> None:
        self.__backend.bulk_insert(self, data)

    def bulk_delete(self, ids: list[PrimaryKeyType]) -> None:
        self.__backend.bulk_delete(self, ids)

    def get_primary_key_name(self) -> str:
        meta = get_model_definition(self.__model)
        return meta.primary_key

    def get_primary_key(self, model_data: Mapping) -> PrimaryKeyType:
        pk_name = self.get_primary_key_name()
        return model_data[pk_name]

    def to_pydantic(
        self, data: Mapping, options: ToPydanticOptions | None = None
    ) -> BaseModel:
        if options and options.no_validation:
            # Use model_construct to skip validation
            return self.__model.model_construct(**data)
        return self.__model(**data)


def create_in_layers_model(
    pydantic_model: BaseModel, backend: BackendProtocol
) -> InLayersModel:
    """
    Public helper to wrap a Pydantic model into an InLayersModel with the provided backend.
    """
    return _InLayersModelImpl(pydantic_model, backend)


class _ModelCruds:
    def __init__(self, methods: Mapping[str, Any]):
        for key, value in methods.items():
            setattr(self, key, value)


def create_model_cruds(  # noqa: C901
    model_or_getter: InLayersModel | Callable[[], InLayersModel],
    options: Mapping[str, Any] | None = None,
):
    def _get_model() -> InLayersModel:
        if callable(model_or_getter):
            return model_or_getter()
        return model_or_getter

    raw_overrides = (options or {}).get("overrides") or {}
    if isinstance(raw_overrides, Mapping):
        overrides = dict(raw_overrides)
    else:
        overrides = {
            key: getattr(raw_overrides, key)
            for key in dir(raw_overrides)
            if not key.startswith("_") and callable(getattr(raw_overrides, key))
        }

    def get_model():
        return _get_model()

    def _default_create(data=None, **kwargs):
        inst = _get_model().create(data, **kwargs)
        return inst.to_pydantic()

    def _default_retrieve(id):
        inst = _get_model().retrieve(id)
        return None if inst is None else inst.to_pydantic(Box(no_validation=True))

    def _default_update(id, **kwargs):
        inst = _get_model().update(id, **kwargs)
        return inst.to_pydantic()

    def _default_delete(id):
        _get_model().delete(id)

    def _default_search(query):
        result = _get_model().search(query)
        return Box(
            instances=[
                instance.to_pydantic(Box(no_validation=True))
                for instance in result.instances
            ],
            page=getattr(result, "page", None),
        )

    def _default_count() -> int:
        return _get_model().count()

    def _default_bulk_insert(data: list[Mapping]) -> None:
        _get_model().bulk_insert(data)

    def _default_bulk_delete(ids: list[PrimaryKeyType]) -> None:
        _get_model().bulk_delete(ids)

    return _ModelCruds(
        {
            "get_model": get_model,
            "create": overrides.get("create") or _default_create,
            "retrieve": overrides.get("retrieve") or _default_retrieve,
            "update": overrides.get("update") or _default_update,
            "delete": overrides.get("delete") or _default_delete,
            "search": overrides.get("search") or _default_search,
            "count": overrides.get("count") or _default_count,
            "bulk_insert": overrides.get("bulk_insert") or _default_bulk_insert,
            "bulk_delete": overrides.get("bulk_delete") or _default_bulk_delete,
        }
    )


class ModelsServices:

    def __init__(self, context):
        self.__context = context

    def get_model_backend(
        self, model_definition: ModelDefinition  # noqa: ARG002
    ) -> BackendProtocol:
        return MemoryBackend()


def create(context) -> ModelsServices:
    return ModelsServices(context)
