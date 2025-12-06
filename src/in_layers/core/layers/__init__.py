from __future__ import annotations

from ..protocols import CoreNamespace
from . import (
    features,
    services,
)

name = CoreNamespace.layers.value
__all__ = ["features", "name", "services"]
