from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ..libs import combine_cross_layer_props, is_cross_layer_props
from ..protocols import CrossLayerProps, Logger, LogLevelNames


def default_get_function_wrap_log_level(layer_name: str) -> LogLevelNames:
    if layer_name in ("features", "entries"):
        return LogLevelNames.info
    if layer_name == "services":
        return LogLevelNames.trace
    return LogLevelNames.debug


def combine_logging_props(
    logger: Logger, cross_layer_props: CrossLayerProps | None = None
) -> Mapping[str, Any]:

    base: CrossLayerProps = {"logging": {"ids": logger.get_ids()}}
    final = combine_cross_layer_props(base, cross_layer_props or {})  # type: ignore[arg-type]
    return final["logging"]


def cap_for_logging(input: Any, max_size: int = 50000) -> Any:
    def safe_stringify(obj: Any) -> str:
        try:
            return json.dumps(obj, default=str)
        except Exception:
            return "[Unserializable]"

    if not isinstance(input, (list, dict)):
        return input
    if len(safe_stringify(input)) <= max_size:
        return input
    if isinstance(input, list):
        build: list[Any] = []
        for item in input:
            candidate = [*build, item]
            if (
                len(
                    safe_stringify(
                        [*candidate, f"[truncated, original length: {len(input)}]"]
                    )
                )
                > max_size
            ):
                return [*build, f"[truncated, original length: {len(input)}]"]
            build.append(item)
        return build
    keys = list(input.keys())
    out: dict[str, Any] = {}
    for k in keys:
        out[k] = input[k]
        truncated = dict(out)
        truncated["[truncated]"] = f"original keys: {len(keys)}"
        if len(safe_stringify(truncated)) > max_size:
            return truncated
    return out


_CROSS_PARAM_NAMES = {
    "crossLayer",
    "cross_layer",
    "crossLayerProps",
    "cross_layer_props",
}


def extract_cross_layer_props(
    args: list[Any],
    kwargs: Mapping[str, Any] | None = None,
) -> tuple[list[Any], dict[str, Any], CrossLayerProps | None]:
    """
    Extract cross-layer props from the last positional argument if present,
    otherwise look for a named kwarg using any supported cross param name.
    Returns (args_without_cross, kwargs_without_cross, cross_or_none).
    """
    args_no_cross = list(args) if args else []
    kwargs_copy: dict[str, Any] = dict(kwargs or {})
    cross_layer_props: CrossLayerProps | None = None
    # 1) Check last positional
    if args_no_cross:
        last = args_no_cross[-1]
        if is_cross_layer_props(last):  # type: ignore[arg-type]
            cross_layer_props = last  # type: ignore[assignment]
            args_no_cross = args_no_cross[:-1]
            return args_no_cross, kwargs_copy, cross_layer_props
    # 2) Check kwargs for known names
    for name in _CROSS_PARAM_NAMES:
        if name in kwargs_copy:
            cross = kwargs_copy[name]
            if is_cross_layer_props(cross):
                cross_layer_props = cross  # type: ignore[assignment]
                kwargs_copy.pop(name)  # type: ignore[assignment]
            else:
                kwargs_copy.pop(name)  # type: ignore[assignment]
    return args_no_cross, kwargs_copy, cross_layer_props
