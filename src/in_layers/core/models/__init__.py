from ..protocols import CoreNamespace
from . import services
from .services import create_in_layers_model, create_model_cruds

name = CoreNamespace.models.value
__all__ = ["create_in_layers_model", "create_model_cruds", "name", "services"]
