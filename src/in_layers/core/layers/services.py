from __future__ import annotations

from collections.abc import Mapping
from types import ModuleType
from typing import Any

from ..models.libs import is_model_class
from ..protocols import Domain, LayerContext, ServicesContext


def _iter_models_from_module(module: ModuleType) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in dir(module):
        try:
            attr = getattr(module, name)
        except Exception:  # noqa: S112
            continue
        if is_model_class(attr):
            result[name] = attr
    return result


def _iter_models_from_mapping(mapping: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for k, v in mapping.items():
        if is_model_class(v):
            result[str(k)] = v
    return result


def _iter_models_from_object(obj: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in dir(obj):
        if name.startswith("_"):
            continue
        try:
            attr = getattr(obj, name)
        except Exception:  # noqa: S112
            continue
        if is_model_class(attr):
            result[name] = attr
    return result


def _iter_model_candidates(container: Any) -> Mapping[str, Any]:
    """
    Discover model classes from a container. Supports modules, mappings, and objects.
    """
    if container is None:
        return {}
    if isinstance(container, ModuleType):
        return _iter_models_from_module(container)
    if isinstance(container, Mapping):
        return _iter_models_from_mapping(container)  # type: ignore[arg-type]
    return _iter_models_from_object(container)


def _build_domain_model_index(
    context: ServicesContext,
) -> Mapping[str, Mapping[str, Any]]:
    domains = getattr(context.config.in_layers_core, "domains", []) or []
    domain_name_to_models: dict[str, dict[str, Any]] = {}
    for domain in domains:
        domain_name = getattr(domain, "name", None)
        if not domain_name:
            continue
        models_attr = getattr(domain, "models", None)
        domain_name_to_models[domain_name] = dict(_iter_model_candidates(models_attr))
    return domain_name_to_models


class _ModelResolver:
    def __init__(self, index: Mapping[str, Mapping[str, Any]]):
        self.__index = index

    def get_model(self, namespace: str, model_name: str) -> Any:
        models_for_namespace = self.__index.get(namespace)
        if not models_for_namespace:
            raise KeyError(f"No models found for namespace '{namespace}'")
        model_cls = models_for_namespace.get(model_name)
        if not model_cls:
            raise KeyError(f"Model '{model_name}' not found in namespace '{namespace}'")
        return model_cls


class LayersServices:
    def get_model_props(self, context: ServicesContext):
        """
        Provide model props needed by domains to construct models.
        For the Python port, we expose only `context` and a `get_model` resolver.
        """
        resolver = _ModelResolver(_build_domain_model_index(context))

        return {
            "context": context,
            "get_model": resolver.get_model,
        }

    def load_layer(self, app: Domain, layer: str, context: LayerContext):
        layer_instance = getattr(app, layer, None)
        if not layer_instance or not hasattr(layer_instance, "create"):
            return None
        instance = layer_instance.create(context)
        if instance is None:
            raise RuntimeError(
                f"App {app.get('name')} did not return an instance layer {layer}"
            )
        return instance


def create() -> LayersServices:
    return LayersServices()
