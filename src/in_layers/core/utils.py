from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any


class AttrMap:
    def __init__(self, mapping: Mapping[str, Any]):
        self._m = mapping

    def __getattr__(self, name: str) -> Any:
        try:
            value = self._m[name]
        except KeyError as e:
            raise AttributeError(name) from e
        return self._wrap(value)

    def __getitem__(self, key: str) -> Any:
        return self._m[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._m)

    def __repr__(self) -> str:
        return f"AttrMap({self._m!r})"

    def keys(self):
        return self._m.keys()

    def items(self):
        for k, v in self._m.items():
            yield k, self._wrap(v)

    def get(self, key: str, default: Any = None) -> Any:
        return self._wrap(self._m.get(key, default))

    def _wrap(self, value: Any) -> Any:
        if isinstance(value, dict):
            return AttrMap(value)
        return value
