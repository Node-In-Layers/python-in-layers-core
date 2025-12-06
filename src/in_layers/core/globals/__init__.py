from __future__ import annotations

from ..protocols import CoreNamespace
from . import (
    features,
    services,
)

name = CoreNamespace.globals.value
__all__ = ["features", "name", "services"]
