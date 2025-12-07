from __future__ import annotations

from ..protocols import Domain, LayerContext, ServicesContext


class LayersServices:
    def get_model_props(self, context: ServicesContext):
        raise NotImplementedError("Model support not implemented in Python port")

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
