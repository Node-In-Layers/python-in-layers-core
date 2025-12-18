from ..protocols import CoreNamespace
from . import services

name = CoreNamespace.models.value
__all__ = ["name", "services"]
