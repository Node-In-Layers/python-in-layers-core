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
    LayersFeatures,
)


def create(context: FeaturesContext) -> LayersFeatures:
    def _get_layer_context(
        common_context: Mapping[str, Any], layer: Mapping[str, Any] | None
    ):
        if layer:
            merged = deepcopy(common_context)
            for k, v in layer.items():
                merged[k] = v
            return merged
        return common_context

    async def _load_layer(
        app: Mapping[str, Any],
        current_layer: str,
        common_context: Mapping[str, Any],
        previous_layer: Mapping[str, Any] | None,
    ):
        layer_context1 = _get_layer_context(common_context, previous_layer)
        layer_logger = (
            context.root_logger.get_logger(Box(layer_context1))
            .get_app_logger(app["name"])
            .get_layer_logger(current_layer)
        )
        layer_context = dict(layer_context1)
        layer_context["log"] = layer_logger

        logger_ids = layer_logger.get_ids()
        ignore_layer_functions = (
            context.config[CoreNamespace.root.value].logging.get(
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

                    def _make_wrapped(f):
                        def _inner2(*args, **kwargs):  # noqa: ARG001
                            args_no_cross, cross = extract_cross_layer_props(list(args))
                            return f(
                                *args_no_cross,
                                cross or {"logging": {"ids": logger_ids}},
                            )

                        return _inner2

                    wrapped_func = _make_wrapped(func)
                    for attr in dir(func):
                        try:  # noqa: SIM105
                            setattr(wrapped_func, attr, getattr(func, attr))
                        except Exception:  # noqa: S110
                            pass
                    domain_data[property_name] = wrapped_func
                final_layer_data[domain_key] = domain_data
            wrapped_context[layer_key] = final_layer_data

        loaded = context.services[CoreNamespace.layers.value].load_layer(
            app, current_layer, Box(wrapped_context)
        )
        if not loaded:
            return {}
        layer_level_key = f"{app['name']}.{current_layer}"
        should_ignore = _get(ignore_layer_functions, layer_level_key)
        final_layer = (
            loaded
            if should_ignore
            else _wrap_layer_functions(
                loaded, layer_logger, app["name"], current_layer, ignore_layer_functions
            )
        )
        return {current_layer: {app["name"]: final_layer}}

    def _wrap_layer_functions(
        loaded_layer: Mapping[str, Any],
        layer_logger,
        app_name: str,
        layer: str,
        ignore_layer_functions: Mapping[str, Any],
    ):
        out = {}
        for property_name, func in loaded_layer.items():
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
        app: Mapping[str, Any],
        composite_layers,
        common_context: Mapping[str, Any],
        previous_layer: Mapping[str, Any] | None,  # noqa: ARG001
        anti_layers_fn,  # noqa: ARG001
    ):
        result = {}
        for layer in composite_layers:
            layer_logger = (
                context.root_logger.get_logger(Box(common_context))
                .get_app_logger(app["name"])
                .get_layer_logger(layer)
            )
            the_context = dict(common_context)
            the_context["log"] = layer_logger
            wrapped_context = the_context
            loaded = context.services[CoreNamespace.layers.value].load_layer(
                app, layer, Box(wrapped_context)
            )
            if loaded:
                ignore_layer_functions = (
                    context.config[CoreNamespace.root.value].logging.get(
                        "ignore_layer_functions"
                    )
                    or {}
                )
                layer_level_key = f"{app['name']}.{layer}"
                should_ignore = _get(ignore_layer_functions, layer_level_key)
                final_layer = (
                    loaded
                    if should_ignore
                    else _wrap_layer_functions(
                        loaded, layer_logger, app["name"], layer, ignore_layer_functions
                    )
                )
                result = {**result, layer: {app["name"]: final_layer}}
        return result

    async def load_layers():
        layers_in_order = context.config[CoreNamespace.root.value].layer_order
        anti_layers = get_layers_unavailable(layers_in_order)
        core_layers_to_ignore = [
            f"services.{CoreNamespace.layers.value}",
            f"services.{CoreNamespace.globals.value}",
            f"features.{CoreNamespace.layers.value}",
            f"features.{CoreNamespace.globals.value}",
        ]
        starting_context: CommonContext = {k: v for k, v in context.items() if k not in core_layers_to_ignore}  # type: ignore[return-value]
        apps = (
            context.config[CoreNamespace.root.value].get("apps")
            or context.config[CoreNamespace.root.value].get("domains")
            or []
        )
        existing_layers = starting_context
        for app in apps:
            previous_layer = {}
            for layer in layers_in_order:
                if isinstance(layer, list):
                    layer_instance = await _load_composite_layer(
                        app,
                        layer,
                        {k: v for k, v in existing_layers.items() if k != "log"},
                        previous_layer,
                        anti_layers,
                    )
                else:
                    layer_instance = await _load_layer(
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
        return existing_layers

    return Box(
        {
            "load_layers": load_layers,
        }
    )


def _get(mapping: Mapping[str, Any], dotted: str, default: Any = None) -> Any:
    cur: Any = mapping
    for part in dotted.split("."):
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur
