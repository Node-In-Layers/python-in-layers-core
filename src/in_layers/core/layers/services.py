from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..protocols import ServicesContext


class LayersServices:
    def get_model_props(self, context: ServicesContext):
        raise NotImplementedError("Model support not implemented in Python port")

    def load_layer(self, app: Mapping[str, Any], layer: str, context: Mapping[str, Any]):
        constructor = app.get(layer)
        if not constructor or "create" not in constructor:
            return None
        instance = constructor.create(context)
        if instance is None:
            raise RuntimeError(
                f"App {app.get('name')} did not return an instance layer {layer}"
            )
        return instance

def create() -> LayersServices:
    return LayersServices()
