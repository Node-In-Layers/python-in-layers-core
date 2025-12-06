from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from box import Box

from .globals import features as globals_features
from .globals import name as globals_name
from .globals import services as globals_services
from .layers import features as layers_features
from .layers import name as layers_name
from .layers import services as layers_services


@dataclass(frozen=True)
class SystemProps:
    environment: str
    config: Mapping[str, Any] | None = None


async def load_system(props: SystemProps):
    global_services = globals_services.create(
        {
            "environment": props.environment,
            "working_directory": os.getcwd(),
        }
    )
    global_features = globals_features.create(
        Box(
            {
                "services": {
                    globals_name: global_services,
                }
            }
        )
    )
    globals_context = await global_features.load_globals(
        props.config or props.environment
    )

    # layers

    the_layers_services = layers_services.create()
    the_layers_features = layers_features.create(
        Box(
            {
                **globals_context,
                "services": {
                    layers_name: the_layers_services,
                },
            }
        )
    )
    layers_loaded = await the_layers_features.load_layers()
    try:
        if "services" in layers_loaded and layers_name in layers_loaded.services:
            del layers_loaded.services[layers_name]
    except Exception:  # noqa: S110
        pass
    return layers_loaded
