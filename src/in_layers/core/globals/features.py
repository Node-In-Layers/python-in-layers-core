from __future__ import annotations

from typing import Any

from box import Box

from ..libs import is_config, validate_config
from ..protocols import CommonContext, CoreNamespace, FeaturesContext

globals_name = CoreNamespace.globals.value


class GlobalsFeatures:
    def __init__(self, context: FeaturesContext):
        self.context = context

    def load_globals(self, environment_or_config: Any) -> CommonContext:
        services = self.context.services[globals_name]
        if not services:
            raise RuntimeError(f"Services for {globals_name} not found")
        config = (
            environment_or_config
            if is_config(environment_or_config)
            else services.load_config()
        )
        validate_config(config)
        common_globals: CommonContext = Box(
            config=config,
            root_logger=services.get_root_logger(),
            constants=services.get_constants(),
        )
        return common_globals


def create(context: FeaturesContext) -> GlobalsFeatures:
    return GlobalsFeatures(context)
