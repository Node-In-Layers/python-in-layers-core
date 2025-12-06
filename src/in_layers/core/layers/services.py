from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from box import Box

from ..protocols import LayersServices, ServicesContext


def create() -> LayersServices:
    def get_model_props(context: ServicesContext):
        raise NotImplementedError("Model support not implemented in Python port")

    def load_layer(app: Mapping[str, Any], layer: str, context: Mapping[str, Any]):
        constructor = app.get(layer)
        if not constructor or "create" not in constructor:
            return None
        instance = constructor.create(context)
        if instance is None:
            raise RuntimeError(
                f"App {app.get('name')} did not return an instance layer {layer}"
            )
        return instance

    return Box(
        {
            "get_model_props": get_model_props,
            "load_layer": load_layer,
        }
    )
