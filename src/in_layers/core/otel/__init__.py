from __future__ import annotations

from ..protocols import CoreNamespace
from . import libs, services, types

name = CoreNamespace.otel.value

__all__ = ["libs", "name", "services", "types"]
