from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, cast

from box import Box

from .globals import features as globals_features
from .globals import name as globals_name
from .globals import services as globals_services
from .layers import features as layers_features
from .layers import name as layers_name
from .layers import services as layers_services
from .protocols import Config, FeaturesContext, GlobalsServicesProps


@dataclass(frozen=True)
class SystemProps:
    environment: str
    config: Config | None = None


def load_system(props: SystemProps) -> Any:
    global_services = globals_services.create(
        GlobalsServicesProps(
            environment=props.environment,
            working_directory=os.getcwd(),
        )
    )
    global_features = globals_features.create(
        cast(
            FeaturesContext,
            Box(
                services={
                    globals_name: global_services,
                },
            ),
        ),
    )
    globals_context = global_features.load_globals(props.config or props.environment)

    # layers

    the_layers_services = layers_services.create()
    the_layers_features = layers_features.create(
        cast(
            FeaturesContext,
            cast(Box, globals_context)
            + Box(
                {
                    "services": {
                        layers_name: the_layers_services,
                    },
                }
            ),
        )
    )
    layers_loaded = the_layers_features.load_layers()
    try:
        if "services" in layers_loaded and layers_name in layers_loaded.services:
            del layers_loaded.services[layers_name]
    except Exception:  # noqa: S110
        pass
    return layers_loaded
