from __future__ import annotations

import functools
import inspect
from collections.abc import Mapping
from typing import Any

from box import Box

from ..globals.libs import extract_cross_layer_props
from ..libs import (
    create_cross_layer_props,
    get_layers_unavailable,
    get_otel_forward_baggage_from_config,
    normalize_cross_layer_props,
)
from ..models.libs import get_model_definition, is_model_class
from ..models.protocols import InLayersModel, PrimaryKeyType
from ..models.services import create_in_layers_model, create_model_cruds
from ..protocols import (
    CommonContext,
    CoreNamespace,
    FeaturesContext,
)
from ..utils import rgetattr

_CROSS_PARAM_NAMES = {
    "crossLayer",
    "cross_layer",
    "crossLayerProps",
    "cross_layer_props",
}

_INTERNAL_MODELS_KEY = "__in_layers_models"


class _WrappedCruds:
    def __init__(self, methods: Mapping[str, Any]):
        for key, value in methods.items():
            setattr(self, key, value)


def _core_model_settings(config: Any) -> Mapping[str, Any]:
    core = getattr(config, "in_layers_core", {}) or {}
    models = getattr(core, "models", {}) or {}
    merged = {}
    if isinstance(models, Mapping):
        merged.update(models)
    else:
        merged.update(getattr(models, "__dict__", {}))
    if isinstance(core, Mapping):
        merged.update(core)
    else:
        merged.update(getattr(core, "__dict__", {}))
    return Box(merged)


class _CrudsWrapper:
    def __init__(self, in_layers_model: InLayersModel):
        self._im = in_layers_model

    def get_model(self):
        return self._im

    def create(self, data=None, **kwargs):
        inst = self._im.create(data, **kwargs)
        return inst.to_pydantic()

    def retrieve(self, id):
        inst = self._im.retrieve(id)
        return None if inst is None else inst.to_pydantic(Box(no_validation=True))

    def update(self, id, **kwargs):
        inst = self._im.update(id, **kwargs)
        return inst.to_pydantic()

    def delete(self, id):
        self._im.delete(id)

    def search(self, query):
        res = self._im.search(query)
        instances = [i.to_pydantic(Box(no_validation=True)) for i in res.instances]
        page = getattr(res, "page", None)
        return Box(instances=instances, page=page)

    def count(self) -> int:
        return self._im.count()

    def bulk_insert(self, data: list[Mapping]) -> list[Mapping]:
        return self._im.bulk_insert(data)

    def bulk_delete(self, ids: list[PrimaryKeyType]) -> None:
        self._im.bulk_delete(ids)


class _FeatureCruds:
    def __init__(self, base_crud):
        self._base = base_crud

    def get_model(self):
        return self._base.get_model()

    def create(self, data=None, **kwargs):
        return self._base.create(data, **kwargs)

    def retrieve(self, id):
        return self._base.retrieve(id)

    def update(self, id, **kwargs):
        return self._base.update(id, **kwargs)

    def delete(self, id):
        return self._base.delete(id)

    def search(self, query):
        return self._base.search(query)

    def count(self) -> int:
        return self._base.count()

    def bulk_insert(self, data: list[Mapping]) -> list[Mapping]:
        return self._base.bulk_insert(data)

    def bulk_delete(self, ids: list[PrimaryKeyType]) -> None:
        return self._base.bulk_delete(ids)


def _is_model_services_cruds_enabled(config: Any) -> bool:
    settings = _core_model_settings(config)
    return bool(
        settings.get("model_services_cruds")
        or settings.get("model_features_cruds")
        or settings.get("model_cruds")
    )


def _is_model_features_cruds_enabled(config: Any) -> bool:
    settings = _core_model_settings(config)
    return bool(settings.get("model_features_cruds") or settings.get("model_cruds"))


def _get_no_model_log_wrap(config: Any) -> bool:
    settings = _core_model_settings(config)
    return bool(settings.get("no_model_log_wrap"))


def _resolve_backend_for_model(
    features: LayersFeatures, layer_context: Mapping[str, Any], model_cls: Any
):
    backend = _resolve_backend_from_domain_provider(features, layer_context, model_cls)
    if backend is None:
        backend = _resolve_backend_from_core(features, model_cls)
    return backend


def _resolve_backend_from_domain_provider(
    features: LayersFeatures, layer_context: Mapping[str, Any], model_cls: Any
):
    backend_domain = rgetattr(
        features, "context.config.in_layers_core.models.model_backend", None
    )
    if not backend_domain:
        return None
    services_map = layer_context.get("services", None)
    if not services_map:
        raise ValueError("Services map is not found in layer context.")
    provider = services_map.get(backend_domain)
    if not provider:
        raise ValueError(f"Provider {backend_domain} does not have a services layer.")
    return provider.get_model_backend(get_model_definition(model_cls))


def _resolve_backend_from_core(features: LayersFeatures, model_cls: Any):
    core_factory = features.context.services.get(CoreNamespace.models.value)  # type: ignore[index]
    if not core_factory:
        raise ValueError(f"Model {model_cls} has no core factory")
    return core_factory.get_model_backend(get_model_definition(model_cls))


def _build_in_layers_models_for_app(
    features: LayersFeatures,
    layer_context_local: Mapping[str, Any],
    discovered_local: Mapping[str, Any],
) -> Mapping[str, Any]:
    wrapped: dict[str, Any] = {}
    for name, model_cls in discovered_local.items():
        backend = _resolve_backend_for_model(
            features, Box(layer_context_local), model_cls
        )
        if backend is None:
            try:
                meta = get_model_definition(model_cls)
                plural = Box(meta).plural_name if meta else name
                wrapped[plural] = model_cls
            except Exception:
                wrapped[name] = model_cls
            continue
        simple = create_in_layers_model(model_cls, backend)
        try:
            plural = Box(simple.get_model_definition()).plural_name
        except Exception:
            try:
                meta = get_model_definition(model_cls)
                plural = Box(meta).plural_name if meta else name
            except Exception:
                plural = name
        wrapped[plural] = simple
    return Box(wrapped)


def _build_services_cruds(simple_models_map: Mapping[str, Any]) -> Mapping[str, Any]:
    cruds_wrappers: dict[str, Any] = {}
    for plural_name, simple_model in dict(simple_models_map).items():
        cruds_wrappers[plural_name] = _CrudsWrapper(simple_model)
    return Box(cruds_wrappers)


def _resolve_model_cruds_factory(
    features: LayersFeatures,
    layer_name: str,
    domain_name: str,
    model_name: str,
):
    config = _core_model_settings(features.context.config)
    factory_config = config.get("model_cruds_factory")
    if callable(factory_config):
        return factory_config
    if isinstance(factory_config, list):
        for override in factory_config:
            if override.get("layer") not in (None, layer_name):
                continue
            if override.get("domain") not in (None, domain_name):
                continue
            if override.get("model") not in (None, model_name):
                continue
            factory = override.get("factory")
            if callable(factory):
                return factory
    return lambda model, _context, options=None: create_model_cruds(model, options)


def _maybe_add_services_cruds(
    features: LayersFeatures,
    layer_context: Mapping[str, Any],
    app: Mapping[str, Any],
    final_layer: Mapping[str, Any],
) -> Mapping[str, Any]:
    if not _is_model_services_cruds_enabled(features.context.config):
        return final_layer
    models_ctx = layer_context.get("models", {})  # type: ignore[assignment]
    domain_models_entry = (
        models_ctx.get(app.name, {}) if isinstance(models_ctx, Mapping) else {}
    )
    simple_models = (
        domain_models_entry.get(_INTERNAL_MODELS_KEY)
        if isinstance(domain_models_entry, Mapping)
        else Box({})
    )
    cruds_wrappers = {
        plural_name: _resolve_model_cruds_factory(
            features, "services", app.name, plural_name
        )(
            simple_model,
            layer_context,
        )
        for plural_name, simple_model in dict(simple_models or {}).items()
    }
    new_final = _coerce_layer_to_mapping(final_layer)
    new_final["cruds"] = Box(cruds_wrappers)
    return new_final


def _maybe_add_features_cruds(
    features: LayersFeatures,
    layer_context: Mapping[str, Any],
    app: Mapping[str, Any],
    final_layer: Mapping[str, Any],
) -> Mapping[str, Any]:
    if not _is_model_features_cruds_enabled(features.context.config):
        return final_layer
    service_cruds = _get_service_cruds_for_domain(layer_context, app)
    if not isinstance(service_cruds, Mapping):
        return final_layer
    feature_wrappers = {
        plural_name: _resolve_model_cruds_factory(
            features, "features", app.name, plural_name
        )(
            lambda svc_crud=svc_crud: svc_crud.get_model(),
            layer_context,
            {"overrides": svc_crud},
        )
        for plural_name, svc_crud in dict(service_cruds).items()
    }
    new_final = _coerce_layer_to_mapping(final_layer)
    new_final["cruds"] = Box(feature_wrappers)
    return new_final


def _is_features_cruds_enabled(features: LayersFeatures) -> bool:
    try:
        return _is_model_features_cruds_enabled(features.context.config)
    except Exception:
        return False


def _get_service_cruds_for_domain(
    layer_context: Mapping[str, Any], app: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    services_map = layer_context.get("services", {})  # type: ignore[assignment]
    service_for_domain = (
        services_map.get(app.name) if isinstance(services_map, Mapping) else None
    )
    service_cruds = (
        service_for_domain.get("cruds")
        if isinstance(service_for_domain, Mapping)
        else None
    )
    return service_cruds if isinstance(service_cruds, Mapping) else None


def _wrap_service_cruds_for_features(
    service_cruds: Mapping[str, Any],
) -> Mapping[str, Any]:
    feature_wrappers: dict[str, Any] = {}
    for plural_name, svc_crud in dict(service_cruds).items():
        feature_wrappers[plural_name] = _FeatureCruds(svc_crud)
    return feature_wrappers


def _create_wrapper_with_metadata(original_func: Any, inner_callable: Any) -> Any:
    """
    Return a new wrapper with original_func's metadata/signature, adding
    an optional cross_layer_props parameter when not explicitly present.
    """
    wrapped = functools.wraps(original_func)(inner_callable)
    try:
        sig = getattr(original_func, "__signature__", inspect.signature(original_func))
        params = list(sig.parameters.values())
        has_cross = any(p.name in _CROSS_PARAM_NAMES for p in params)
        if not has_cross:
            new_param = inspect.Parameter(
                "cross_layer_props",
                kind=inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=None,
            )
            wrapped.__signature__ = sig.replace(parameters=[*params, new_param])
        else:
            wrapped.__signature__ = sig
        wrapped.__wrapped__ = getattr(original_func, "__wrapped__", original_func)
    except Exception:  # noqa: S110
        pass
    return wrapped


def _iter_properties_for_wrap(obj: Any):
    if isinstance(obj, Mapping):
        yield from obj.items()
        return
    for name in dir(obj):
        if name.startswith("_"):
            continue
        try:
            attr = getattr(obj, name)
        except Exception:  # noqa: S112
            continue
        if callable(attr):
            yield name, attr


def _coerce_layer_to_mapping(layer: Any) -> dict[str, Any]:
    if isinstance(layer, Mapping):
        return dict(layer)
    return {name: value for name, value in _iter_properties_for_wrap(layer)}


def _get_params_for_func(fn: Any) -> list[inspect.Parameter]:
    return list(inspect.signature(fn).parameters.values())


def _get_explicit_positional_params(
    params: list[inspect.Parameter],
) -> list[inspect.Parameter]:
    return [
        p
        for p in params
        if p.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]


def _has_var_positional(params: list[inspect.Parameter]) -> bool:
    return any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params)


def _trim_surplus_args_for_params(
    pos_args: list[Any], params: list[inspect.Parameter]
) -> list[Any]:
    if _has_var_positional(params):
        return pos_args
    explicit = _get_explicit_positional_params(params)
    surplus = len(pos_args) - len(explicit)
    return pos_args[surplus:] if surplus > 0 else pos_args


def _find_cross_kw_name_in_params(params: list[inspect.Parameter]) -> str | None:
    for p in params:
        if p.name in _CROSS_PARAM_NAMES:
            return p.name
    return None


def _call_with_optional_cross(
    f,
    args_no_cross: list[Any],
    kwargs_no_cross: dict[str, Any],
    cross_layer_props: Mapping[str, Any] | None,
) -> Any:
    params = _get_params_for_func(f)
    args_no_cross = _trim_surplus_args_for_params(args_no_cross, params)
    if cross_layer_props is None:
        return f(*args_no_cross, **kwargs_no_cross)

    cross_kw = _find_cross_kw_name_in_params(params)
    if cross_kw:
        if cross_kw not in kwargs_no_cross:
            kwargs_no_cross = dict(kwargs_no_cross)
            kwargs_no_cross[cross_kw] = cross_layer_props
        return f(*args_no_cross, **kwargs_no_cross)

    # No cross-like kw param; avoid positional injection if *args is present
    if _has_var_positional(params):
        return f(*args_no_cross, **kwargs_no_cross)

    explicit = _get_explicit_positional_params(params)
    if len(args_no_cross) + 1 == len(explicit):
        # Check if the parameter that would receive cross_layer_props is already in kwargs
        # to avoid "multiple values for argument" error
        next_param_index = len(args_no_cross)
        if next_param_index < len(explicit):
            next_param_name = explicit[next_param_index].name
            if next_param_name in kwargs_no_cross:
                # Can't inject positionally - would conflict with keyword arg
                return f(*args_no_cross, **kwargs_no_cross)
        return f(*args_no_cross, cross_layer_props, **kwargs_no_cross)
    return f(*args_no_cross, **kwargs_no_cross)


def _make_passthrough_for_log(f):
    def _inner(log, *args, **kwargs):  # noqa: ARG001
        return f(*args, **kwargs)

    return _create_wrapper_with_metadata(f, _inner)


def _should_copy_direct_layer_key(key: str) -> bool:
    return key in (
        "_logging",
        "root_logger",
        "log",
        "constants",
        "config",
        "models",
        "get_models",
        "cruds",
    )


def _wrap_domain_mapping_for_load(
    features: LayersFeatures,
    domain_value: Mapping[str, Any],
    logger_ids: Any,
) -> Mapping[str, Any]:
    domain_data: dict[str, Any] = {}
    for property_name, func in domain_value.items():
        if not callable(func):
            domain_data[property_name] = func
            continue
        wrapped_func = features._make_wrapped(func, logger_ids)
        domain_data[property_name] = wrapped_func
    return domain_data


def _build_wrapped_context_for_load(
    features: LayersFeatures,
    ctx: Mapping[str, Any],
    logger_ids: Any,
) -> Mapping[str, Any]:
    wrapped: dict[str, Any] = {}
    for layer_key, layer_data in ctx.items():
        if _should_copy_direct_layer_key(layer_key) or not isinstance(
            layer_data, Mapping
        ):
            wrapped[layer_key] = layer_data
            continue
        final_layer_data: dict[str, Any] = {}
        for domain_key, domain_value in layer_data.items():
            if not isinstance(domain_value, Mapping):
                final_layer_data[domain_key] = domain_value
                continue
            final_layer_data[domain_key] = _wrap_domain_mapping_for_load(
                features,
                domain_value,
                logger_ids,
            )
        wrapped[layer_key] = final_layer_data
    return wrapped


class LayersFeatures:
    def __init__(self, context: FeaturesContext):
        self.context = context
        self._finalized_services_domains: Mapping[str, Any] = {}
        self._finalized_features_domains: Mapping[str, Any] = {}
        self._finalized_models_domains: Mapping[str, Any] = {}
        ordered_layers: list[str] = []
        for layer in context.config.in_layers_core.layer_order:
            if isinstance(layer, list):
                ordered_layers.extend(layer)
            else:
                ordered_layers.append(layer)
        self._ordered_layers = ordered_layers
        self._features_layer_index = (
            ordered_layers.index("features") if "features" in ordered_layers else -1
        )

    def _get_layer_context(
        self, common_context: Mapping[str, Any], layer: Mapping[str, Any] | None
    ) -> Mapping[str, Any]:
        if layer:
            merged = Box(common_context)
            return merged + Box(layer)
        return common_context

    def _can_access_features(self, layer_name: str) -> bool:
        if self._features_layer_index == -1:
            return False
        try:
            return self._ordered_layers.index(layer_name) >= self._features_layer_index
        except ValueError:
            return False

    def _get_services(self, domain: str):
        return self._finalized_services_domains.get(domain)

    def _get_features(self, domain: str):
        return self._finalized_features_domains.get(domain)

    def _add_finalized_domain_getters(
        self, layer_context: Mapping[str, Any], current_layer: str
    ) -> Mapping[str, Any]:
        next_services = dict(layer_context.get("services", {}))
        next_services["get_services"] = self._get_services
        next_services["getServices"] = self._get_services
        updated = dict(layer_context)
        updated["services"] = Box(next_services)
        if not self._can_access_features(current_layer):
            return Box(updated)
        next_features = dict(layer_context.get("features", {}))
        next_features["get_features"] = self._get_features
        next_features["getFeatures"] = self._get_features
        updated["features"] = Box(next_features)
        return Box(updated)

    def _make_wrapped(self, f, logger_ids):
        def _inner2(*args, **kwargs):
            args_no_cross, kwargs_no_cross, cross_layer_props = (
                extract_cross_layer_props(list(args), dict(kwargs))
            )
            # Normalize to Box here so we never pass a Pydantic/object instance into combine or the user's function
            cross_as_box = normalize_cross_layer_props(cross_layer_props)
            helper_logger = Box(get_ids=lambda: logger_ids)
            combined = create_cross_layer_props(
                helper_logger,
                cross_as_box or {},
                get_otel_forward_baggage_from_config(self.context.config),
            )
            incoming_overrides = (
                cross_as_box.get("logging", {}).get("overrides")
                if cross_as_box is not None
                else None
            )
            if incoming_overrides is not None:
                combined = Box(
                    {
                        **{k: v for k, v in combined.items() if k != "logging"},
                        "logging": {
                            **dict(combined.get("logging", {})),
                            "overrides": incoming_overrides,
                        },
                    },
                    default_box=True,
                )
            return _call_with_optional_cross(
                f, args_no_cross, kwargs_no_cross, combined
            )

        return _create_wrapper_with_metadata(f, _inner2)

    def _wrap_layer_functions(
        self,
        loaded_layer: Any,
        layer_logger,
        app_name: str,
        layer: str,
        ignore_layer_functions: list[str],
    ):
        out: dict[str, Any] = {}
        logger_ids = layer_logger.get_ids()
        for property_name, func in _iter_properties_for_wrap(loaded_layer):
            if not callable(func):
                out[property_name] = func
                continue
            function_level_key = f"{app_name}.{layer}.{property_name}"
            # Always wrap for cross-layer props
            cross_wrapped = self._make_wrapped(func, logger_ids)
            # Only add logging wrapper when not ignored
            if _should_ignore_path(ignore_layer_functions, function_level_key):
                wrapped = cross_wrapped
            else:
                logged_func = layer_logger._log_wrap(
                    property_name, _make_passthrough_for_log(cross_wrapped)
                )
                wrapped = _create_wrapper_with_metadata(func, logged_func)
            out[property_name] = wrapped
        return out

    def _wrap_cruds_container(
        self,
        cruds_container: Mapping[str, Any],
        layer_logger,
        app_name: str,
        layer: str,
        ignore_layer_functions: list[str],
    ) -> Mapping[str, Any]:
        if _get_no_model_log_wrap(self.context.config):
            return Box(cruds_container)
        wrapped_models: dict[str, Any] = {}
        for model_name, cruds in dict(cruds_container).items():
            wrapped_cruds: dict[str, Any] = {}
            for function_name, func in _coerce_layer_to_mapping(cruds).items():
                if function_name == "get_model" or not callable(func):
                    wrapped_cruds[function_name] = func
                    continue
                function_level_key = f"{app_name}.{layer}.{model_name}.{function_name}"
                if _should_ignore_path(ignore_layer_functions, function_level_key):
                    wrapped_cruds[function_name] = func
                    continue
                cross_wrapped = self._make_wrapped(func, layer_logger.get_ids())
                logged_func = layer_logger._log_wrap(
                    f"cruds:{model_name}:{function_name}",
                    _make_passthrough_for_log(cross_wrapped),
                )
                wrapped_cruds[function_name] = _create_wrapper_with_metadata(
                    func, logged_func
                )
            wrapped_models[model_name] = _WrappedCruds(wrapped_cruds)
        return Box(wrapped_models)

    def _inject_models_context(
        self, app: Mapping[str, Any], layer_context: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """
        When in the services layer, attach model discovery helpers and
        precomputed simple model wrappers for the app into the context.
        """
        models_attr = getattr(app, "models", None)
        if models_attr is None:
            return layer_context
        discovered = _iter_model_candidates(models_attr)
        simple_models_box = _build_in_layers_models_for_app(
            self, layer_context, discovered
        )
        self._finalized_models_domains = Box(
            {
                **dict(self._finalized_models_domains),
                app.name: simple_models_box,
            }
        )

        def get_models() -> Mapping[str, Any]:
            return simple_models_box

        models_context = dict(layer_context.get("models", {}))
        models_context[app.name] = {
            **models_context.get(app.name, {}),
            "get_models": get_models,
            _INTERNAL_MODELS_KEY: simple_models_box,  # available for internal use
        }
        new_ctx = dict(layer_context)
        new_ctx["models"] = models_context
        return new_ctx

    def _load_composite_layer(
        self,
        app: Mapping[str, Any],
        composite_layers,
        common_context: Mapping[str, Any],
        previous_layer: Mapping[str, Any] | None,  # noqa: ARG002
        anti_layers_fn,
    ):
        result = {}
        for layer in composite_layers:
            layers_to_remove = anti_layers_fn(layer)
            visible_context = {
                k: v
                for k, v in dict(common_context).items()
                if k not in layers_to_remove
            }
            visible_context = self._add_finalized_domain_getters(visible_context, layer)
            layer_logger = (
                self.context.root_logger.get_logger(
                    Box(
                        visible_context,
                    )
                )
                .get_app_logger(app.name)
                .get_layer_logger(layer)
            )
            the_context = dict(visible_context)
            the_context["log"] = layer_logger
            wrapped_context = the_context
            loaded = self.context.services[CoreNamespace.layers.value].load_layer(
                app,
                layer,
                Box(
                    wrapped_context,
                ),
            )
            if loaded:
                ignore_layer_functions = self.context.config.in_layers_core.logging.get(
                    "ignore_layer_functions", []
                )
                final_layer = self._wrap_layer_functions(
                    loaded, layer_logger, app.name, layer, ignore_layer_functions
                )
                if isinstance(final_layer, Mapping) and "cruds" in final_layer:
                    final_layer = dict(final_layer)
                    final_layer["cruds"] = self._wrap_cruds_container(
                        final_layer["cruds"],
                        layer_logger,
                        app.name,
                        layer,
                        ignore_layer_functions,
                    )
                result = {**result, layer: {app.name: final_layer}}
        return result

    def _load_layer(
        self,
        app: Mapping[str, Any],
        current_layer: str,
        common_context: Mapping[str, Any],
        previous_layer: Mapping[str, Any] | None,
        anti_layers_fn,
    ):
        layers_to_remove = anti_layers_fn(current_layer)
        visible_context = {
            k: v for k, v in dict(common_context).items() if k not in layers_to_remove
        }
        layer_context = self._get_layer_context(visible_context, previous_layer)
        layer_context = self._add_finalized_domain_getters(layer_context, current_layer)

        # If this is the services layer, attach model discovery helpers before
        # building the logger so custom loggers can inspect models too.
        if str(current_layer) == "services":
            layer_context = self._inject_models_context(app, layer_context)

        layer_logger = (
            self.context.root_logger.get_logger(Box(layer_context))
            .get_app_logger(app.name)
            .get_layer_logger(current_layer)
        )
        layer_context = dict(layer_context)
        layer_context["log"] = layer_logger

        logger_ids = layer_logger.get_ids()
        ignore_layer_functions = self.context.config.in_layers_core.logging.get(
            "ignore_layer_functions", []
        )
        wrapped_context = _build_wrapped_context_for_load(
            self, layer_context, logger_ids
        )

        loaded = self.context.services.in_layers_core_layers.load_layer(
            app,
            current_layer,
            Box(wrapped_context),
        )
        if not loaded:
            return {}
        # Inject model CRUD wrappers into services/features when enabled
        final_loaded = loaded
        if str(current_layer) == "services":
            final_loaded = _maybe_add_services_cruds(
                self, layer_context, app, final_loaded
            )
        if str(current_layer) == "features":
            final_loaded = _maybe_add_features_cruds(
                self, layer_context, app, final_loaded
            )
        final_layer = self._wrap_layer_functions(
            final_loaded, layer_logger, app.name, current_layer, ignore_layer_functions
        )
        if isinstance(final_layer, Mapping) and "cruds" in final_layer:
            final_layer = dict(final_layer)
            final_layer["cruds"] = self._wrap_cruds_container(
                final_layer["cruds"],
                layer_logger,
                app.name,
                current_layer,
                ignore_layer_functions,
            )
        return {current_layer: {app.name: final_layer}}

    def load_layers(self):
        layers_in_order = self.context.config.in_layers_core.layer_order
        anti_layers = get_layers_unavailable(layers_in_order)
        core_layers_to_ignore = [
            f"services.{CoreNamespace.layers.value}",
            f"services.{CoreNamespace.globals.value}",
            f"features.{CoreNamespace.layers.value}",
            f"features.{CoreNamespace.globals.value}",
        ]
        starting_context: CommonContext = {k: v for k, v in self.context.items() if k not in core_layers_to_ignore}  # type: ignore[return-value]
        apps = self.context.config.in_layers_core.domains
        existing_layers = starting_context
        for app in apps:
            previous_layer = {}
            for layer in layers_in_order:
                if isinstance(layer, list):
                    layer_instance = self._load_composite_layer(
                        app,
                        layer,
                        {k: v for k, v in existing_layers.items() if k != "log"},
                        previous_layer,
                        anti_layers,
                    )
                else:
                    layer_instance = self._load_layer(
                        app,
                        layer,
                        {k: v for k, v in existing_layers.items() if k != "log"},
                        previous_layer,
                        anti_layers,
                    )
                if not layer_instance:
                    previous_layer = {}
                    continue
                # Deep-merge by layer so we accumulate domains instead of overwriting
                new_context = dict(existing_layers)
                for layer_key, layer_value in layer_instance.items():
                    if (
                        layer_key in new_context
                        and isinstance(new_context[layer_key], Mapping)
                        and isinstance(layer_value, Mapping)
                    ):
                        merged_layer = dict(new_context[layer_key])
                        merged_layer.update(layer_value)
                        new_context[layer_key] = merged_layer
                    else:
                        new_context[layer_key] = layer_value
                if "log" in new_context:
                    new_context = {k: v for k, v in new_context.items() if k != "log"}
                existing_layers = new_context
                self._finalized_services_domains = Box(
                    existing_layers.get("services", {})
                )
                self._finalized_features_domains = Box(
                    existing_layers.get("features", {})
                )
                previous_layer = layer_instance
        self._finalized_services_domains = Box(existing_layers.get("services", {}))
        self._finalized_features_domains = Box(existing_layers.get("features", {}))
        if self._finalized_models_domains:
            existing_layers = {
                **dict(existing_layers),
                "models": Box(self._finalized_models_domains),
            }
        return Box(
            existing_layers,
        )


def create(context: FeaturesContext) -> LayersFeatures:
    return LayersFeatures(context)


def _should_ignore_path(ignore_list: list[str], dotted: str) -> bool:
    if not ignore_list:
        return False
    dotted = dotted.strip().strip(".")
    for pattern in ignore_list:
        if not pattern:
            continue
        pat = str(pattern).strip().strip(".")
        if not pat:
            continue
        if dotted == pat or dotted.startswith(f"{pat}."):
            return True
    return False


def _iter_model_candidates(container: Any) -> dict[str, Any]:
    """
    Discover model classes from a container by inspecting either mapping items
    or public attributes. Returns a name->class dictionary.
    """
    result: dict[str, Any] = {}
    if container is None:
        return result
    # Mapping support
    if isinstance(container, Mapping):
        for k, v in container.items():
            if is_model_class(v):
                result[str(k)] = v
        return result
    # Attribute-based discovery for modules/objects
    for name in dir(container):
        if name.startswith("_"):
            continue
        try:
            attr = getattr(container, name)
        except Exception:  # noqa: S112
            continue
        if is_model_class(attr):
            result[name] = attr
    return result
