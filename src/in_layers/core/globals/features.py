from __future__ import annotations

from typing import Any

from box import Box

from ..libs import is_config, validate_config
from ..protocols import CommonContext, CoreNamespace, FeaturesContext, GlobalsFeatures

globals_name = CoreNamespace.globals.value


def create(context: FeaturesContext) -> GlobalsFeatures:
    services = context.services[globals_name]
    if not services:
        raise RuntimeError(f"Services for {globals_name} not found")

    async def load_globals(environment_or_config: Any):
        config = (
            environment_or_config
            if is_config(environment_or_config)
            else services.load_config()
        )
        validate_config(config)
        common_globals: CommonContext = {
            "config": config,
            "root_logger": services.get_root_logger(),
            "constants": services.get_constants(),
        }
        return common_globals

    return Box(
        {
            "load_globals": load_globals,
        }
    )
