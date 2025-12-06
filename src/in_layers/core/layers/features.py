from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from box import Box

from ..globals.libs import extract_cross_layer_props
from ..libs import get_layers_unavailable
from ..protocols import (
    CommonContext,
    CoreNamespace,
    FeaturesContext,
)


class LayersFeatures:
    def __init__(self, context: FeaturesContext):
        self.context = context

    def _get_layer_context(
        self, common_context: Mapping[str, Any], layer: Mapping[str, Any] | None
    ) -> Mapping[str, Any]:
        if layer:
            merged = deepcopy(common_context)
            for k, v in layer.items():
                merged[k] = v
            return merged
        return common_context

    def _make_wrapped(self, f, logger_ids):
        def _inner2(*args, **kwargs):  # noqa: ARG001
            args_no_cross, cross = extract_cross_layer_props(list(args))
            return f(
                *args_no_cross,
                cross or {"logging": {"ids": logger_ids}},
            )

        return _inner2

    def _wrap_layer_functions(
        self,
        loaded_layer: Any,
        layer_logger,
        app_name: str,
        layer: str,
        ignore_layer_functions: Mapping[str, Any],
    ):
        def _iter_properties(obj: Any):
            if isinstance(obj, Mapping):
                for k, v in obj.items():
                    yield k, v
                return
            # Fallback to attribute-based discovery on class instances
            for name in dir(obj):
                if name.startswith("_"):
                    continue
                try:
                    attr = getattr(obj, name)
                except Exception:
                    continue
                yield name, attr

        out: dict[str, Any] = {}
        for property_name, func in _iter_properties(loaded_layer):
            if not callable(func):
                out[property_name] = func
                continue
            function_level_key = f"{app_name}.{layer}.{property_name}"
            if _get(ignore_layer_functions, function_level_key):
                out[property_name] = func
                continue

            def _make_inner(f):
                def _inner(log, *args, **kwargs):  # noqa: ARG001
                    return f(*args, **kwargs)

                return _inner

            wrapped = layer_logger._log_wrap(property_name, _make_inner(func))
            for attr in dir(func):
                try:  # noqa: SIM105
                    setattr(wrapped, attr, getattr(func, attr))
                except Exception:  # noqa: S110
                    pass
            out[property_name] = wrapped
        return out

    async def _load_composite_layer(
        self,
        app: Mapping[str, Any],
        composite_layers,
        common_context: Mapping[str, Any],
        previous_layer: Mapping[str, Any] | None,
        anti_layers_fn,
    ):
        result = {}
        for layer in composite_layers:
            layer_logger = (
                self.context.root_logger.get_logger(Box(common_context))
                .get_app_logger(app["name"])
                .get_layer_logger(layer)
            )
            the_context = dict(common_context)
            the_context["log"] = layer_logger
            wrapped_context = the_context
            loaded = self.context.services[CoreNamespace.layers.value].load_layer(
                app, layer, Box(wrapped_context)
            )
            if loaded:
                ignore_layer_functions = (
                    self.context.config[CoreNamespace.root.value].logging.get(
                        "ignore_layer_functions"
                    )
                    or {}
                )
                layer_level_key = f"{app['name']}.{layer}"
                should_ignore = _get(ignore_layer_functions, layer_level_key)
                final_layer = (
                    loaded
                    if should_ignore
                    else self._wrap_layer_functions(
                        loaded, layer_logger, app["name"], layer, ignore_layer_functions
                    )
                )
                result = {**result, layer: {app["name"]: final_layer}}
        return result

    async def _load_layer(
        self,
        app: Mapping[str, Any],
        current_layer: str,
        common_context: Mapping[str, Any],
        previous_layer: Mapping[str, Any] | None,
    ):
        layer_context1 = self._get_layer_context(common_context, previous_layer)
        layer_logger = (
            self.context.root_logger.get_logger(Box(layer_context1))
            .get_app_logger(app["name"])
            .get_layer_logger(current_layer)
        )
        layer_context = dict(layer_context1)
        layer_context["log"] = layer_logger

        logger_ids = layer_logger.get_ids()
        ignore_layer_functions = (
            self.context.config[CoreNamespace.root.value].logging.get(
                "ignore_layer_functions"
            )
            or {}
        )
        wrapped_context = {}
        for layer_key, layer_data in layer_context.items():
            if layer_key in (
                "_logging",
                "root_logger",
                "log",
                "constants",
                "config",
                "models",
                "get_models",
                "cruds",
            ):
                wrapped_context[layer_key] = layer_data
                continue
            if not isinstance(layer_data, Mapping):
                wrapped_context[layer_key] = layer_data
                continue
            final_layer_data = {}
            for domain_key, domain_value in layer_data.items():
                if not isinstance(domain_value, Mapping):
                    final_layer_data[domain_key] = domain_value
                    continue
                layer_level_key = f"{domain_key}.{layer_key}"
                if _get(ignore_layer_functions, layer_level_key):
                    final_layer_data[domain_key] = domain_value
                    continue
                domain_data = {}
                for property_name, func in domain_value.items():
                    if not callable(func):
                        domain_data[property_name] = func
                        continue
                    function_level_key = f"{domain_key}.{layer_key}.{property_name}"
                    if _get(ignore_layer_functions, function_level_key):
                        domain_data[property_name] = func
                        continue

                    wrapped_func = self._make_wrapped(func, logger_ids)
                    for attr in dir(func):
                        try:  # noqa: SIM105
                            setattr(wrapped_func, attr, getattr(func, attr))
                        except Exception:  # noqa: S110
                            pass
                    domain_data[property_name] = wrapped_func
                final_layer_data[domain_key] = domain_data
            wrapped_context[layer_key] = final_layer_data

        loaded = self.context.services[CoreNamespace.layers.value].load_layer(
            app, current_layer, Box(wrapped_context)
        )
        if not loaded:
            return {}
        layer_level_key = f"{app['name']}.{current_layer}"
        should_ignore = _get(ignore_layer_functions, layer_level_key)
        final_layer = (
            loaded
            if should_ignore
            else self._wrap_layer_functions(
                loaded, layer_logger, app["name"], current_layer, ignore_layer_functions
            )
        )
        return {current_layer: {app["name"]: final_layer}}

    async def load_layers(self):
        layers_in_order = self.context.config[CoreNamespace.root.value].layer_order
        anti_layers = get_layers_unavailable(layers_in_order)
        core_layers_to_ignore = [
            f"services.{CoreNamespace.layers.value}",
            f"services.{CoreNamespace.globals.value}",
            f"features.{CoreNamespace.layers.value}",
            f"features.{CoreNamespace.globals.value}",
        ]
        starting_context: CommonContext = {k: v for k, v in self.context.items() if k not in core_layers_to_ignore}  # type: ignore[return-value]
        apps = (
            self.context.config[CoreNamespace.root.value].get("apps")
            or self.context.config[CoreNamespace.root.value].get("domains")
            or []
        )
        existing_layers = starting_context
        for app in apps:
            previous_layer = {}
            for layer in layers_in_order:
                if isinstance(layer, list):
                    layer_instance = await self._load_composite_layer(
                        app,
                        layer,
                        {k: v for k, v in existing_layers.items() if k != "log"},
                        previous_layer,
                        anti_layers,
                    )
                else:
                    layer_instance = await self._load_layer(
                        app,
                        layer,
                        {k: v for k, v in existing_layers.items() if k != "log"},
                        previous_layer,
                    )
                if not layer_instance:
                    previous_layer = {}
                    continue
                new_context = {**existing_layers, **layer_instance}
                if "log" in new_context:
                    new_context = {k: v for k, v in new_context.items() if k != "log"}
                existing_layers = new_context
                previous_layer = layer_instance
        return Box(existing_layers)


def create(context: FeaturesContext) -> LayersFeatures:
    return LayersFeatures(context)


def _get(mapping: Mapping[str, Any], dotted: str, default: Any = None) -> Any:
    cur: Any = mapping
    for part in dotted.split("."):
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur
